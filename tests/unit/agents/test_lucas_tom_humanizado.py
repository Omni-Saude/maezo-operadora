"""Tom humanizado do Lucas (DL-0087, decisao do dono de 10/10/2026; `docs/guia-de-tom-dos-agentes.md`).

Tres coisas, contra as funcoes REAIS de texto do grafo:
  1. os exemplos APROVADOS pelo dono saem byte a byte dos fatos correspondentes;
  2. a SUBSTANCIA vale em qualquer texto do Lucas ao beneficiario: nenhum canal proibido ("portal do
     beneficiario"), no maximo um emoji e nunca em escalacao/atraso, a garantia de escalacao na forma
     UNICA, frases de ate 20 palavras, sem "Ola!" no meio da conversa;
  3. as cercas de saida aceitam as formas novas (mes por extenso, DD/MM, "(dados de 08/10)") e continuam
     recusando valor, data e boleto que nao estao nos fatos.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Any, cast

import pytest

from maezo.agents.lucas import graph as lg
from maezo.agents.lucas.graph import (
    ACK_ATENDIMENTO_JA_ABERTO,
    ACK_ESCALACAO,
    ACK_ESCALACAO_RECUSADO,
    GARANTIA_SEM_DECISAO,
    RESPOSTA_INFORMATIVA_RECUSADA,
    SEGUNDA_VIA_CANAIS,
    UM_MES_POR_VEZ,
    VENCIMENTO_NAO_ENCONTRADO,
    LucasState,
    texto_ack_escalacao,
    texto_caso_aberto,
    texto_da_ultima,
    texto_de_esclarecimento,
    texto_mensalidade_em_dia,
    texto_segunda_via,
    texto_segunda_via_repetida,
    texto_valores,
    texto_vencimento,
)
from maezo.agents.lucas.prompts import (
    RECUSA_BOLETO_SEM_FATO,
    RECUSA_DATA_SEM_FATO,
    RECUSA_VALOR_SEM_FATO,
    message_prompt,
    motivo_de_recusa,
)
from maezo.runtime.cobranca_lista import PedidoDeLista

_HOJE = date(2026, 10, 10)

_JULHO = {
    "competencia": "2026-07",
    "situacao": "paga",
    "vencimento": "2026-07-25",
    "valor_total": "9248.38",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-07-29",
    "boleto": "****1111",
}
_OUTUBRO = {
    "competencia": "2026-10",
    "situacao": "paga",
    "vencimento": "2026-10-25",
    "valor_total": "9248.38",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-10-06",
    "boleto": "****0037",
}
_NOVEMBRO = {
    "competencia": "2026-11",
    "situacao": "em_aberto",
    "vencimento": "2026-11-25",
    "valor_total": "9248.38",
    "valor_coparticipacao": "35.10",
    "valor_saldo": "9248.38",
    "boleto": "****0042",
}
_JULHO_2024 = {**_JULHO, "competencia": "2024-07", "vencimento": "2024-07-25", "liquidado_em": "2024-07-29"}


def _estado(*competencias: dict[str, str], cnab: str = "amh-billing:2026-10-08", **extra: Any) -> LucasState:
    return cast(
        LucasState,
        {
            "status_conciliado": True,
            "ciclos_sem_conciliacao": 0,
            "cnab_ref": cnab,
            "competencias_cobranca": list(competencias),
            **extra,
        },
    )


def _fatos(c: dict[str, str] | None, *, pedida: str | None = None, foco: str = "geral", **extra: Any) -> dict:
    return {"competencias": [c] if c else [], "competencia_pedida": pedida, "foco": foco, **extra}


def _cerca(texto: str, fatos: dict[str, Any], rota: str = "mensagem") -> tuple[str, str] | None:
    return motivo_de_recusa(texto, rota, {**fatos, "dados_de": "2026-10-08"})


# --- 1. Os exemplos aprovados pelo dono ---------------------------------------------------------------


def test_valor() -> None:
    fatos = _fatos(_JULHO, pedida="2026-07", foco="valor")
    texto = texto_valores(_estado(_JULHO), fatos, _HOJE)
    assert texto == (
        "Sua mensalidade de julho de 2026 foi de R$ 9.248,38 e já está "
        "paga (você pagou em 29/07). (dados de 08/10)"
    )
    assert _cerca(texto, fatos) is None


def test_valor_de_outro_ano_leva_o_ano() -> None:
    fatos = _fatos(_JULHO_2024, pedida="2024-07", foco="valor")
    texto = texto_valores(_estado(_JULHO_2024), fatos, _HOJE)
    assert texto == (
        "Sua mensalidade de julho de 2024 foi de R$ 9.248,38 e já está paga (você pagou em 29/07/2024). "
        "(dados de 08/10)"
    )
    assert _cerca(texto, fatos) is None


def test_quanto_paguei_em_maio_e_valor() -> None:
    maio = {**_JULHO, "competencia": "2026-05", "vencimento": "2026-05-25", "liquidado_em": "2026-05-20"}
    fatos = _fatos(maio, pedida="2026-05", foco="valor")
    assert texto_valores(_estado(maio), fatos, _HOJE) == (
        "Sua mensalidade de maio de 2026 foi de R$ 9.248,38 e já está "
        "paga (você pagou em 20/05). (dados de 08/10)"
    )


@pytest.mark.parametrize(
    ("copart", "esperado"),
    [
        ("0.00", "Em outubro de 2026 você não teve coparticipação: R$ 0,00. (dados de 08/10)"),
        ("35.10", "Em outubro de 2026 sua coparticipação foi de R$ 35,10. (dados de 08/10)"),
    ],
)
def test_coparticipacao(copart: str, esperado: str) -> None:
    c = {**_OUTUBRO, "valor_coparticipacao": copart}
    fatos = _fatos(c, pedida="2026-10", foco="coparticipacao")
    texto = texto_valores(_estado(c), fatos, _HOJE)
    assert texto == esperado
    assert _cerca(texto, fatos) is None


def test_em_dia() -> None:
    texto = texto_mensalidade_em_dia(_estado(_OUTUBRO, _JULHO), _HOJE)
    assert texto == "Está tudo em dia! Sua última mensalidade (outubro de 2026) já foi paga. (dados de 08/10)"


def test_vencimento_pago_e_a_proxima_nao_gerada() -> None:
    fatos = _fatos(_OUTUBRO)
    texto = texto_vencimento(_estado(_OUTUBRO), fatos, _HOJE)
    assert texto == (
        "A de outubro de 2026 vence em 25/10, mas você já pagou. A de "
        "novembro de 2026 ainda não foi gerada. (dados de 08/10)"
    )
    assert _cerca(texto, fatos) is None


def test_vencimento_em_aberto() -> None:
    fatos = _fatos(_NOVEMBRO)
    texto = texto_vencimento(_estado(_NOVEMBRO), fatos, _HOJE)
    assert texto == (
        "A mensalidade de novembro de 2026 vence em 25/11, no valor de R$ 9.248,38. (dados de 08/10)"
    )
    assert _cerca(texto, fatos) is None


def test_escalacao() -> None:
    texto = texto_ack_escalacao(
        cast(LucasState, {"motivo_humano": "ambiguidade", "status_conciliado": True}), _HOJE
    )
    assert texto == (
        "Vou passar sua dúvida para alguém da nossa equipe, que vai te responder por aqui. "
        "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada."
    )


def test_atraso_escala_sem_afirmar_inadimplencia() -> None:
    estado = cast(
        LucasState,
        {
            "motivo_humano": "inadimplencia_detectada",
            "status_conciliado": False,
            "numero_boleto": "****4821",
            "cnab_ref": "amh-billing:2026-09-30",
        },
    )
    texto = texto_ack_escalacao(estado, _HOJE)
    assert texto == (
        "Vi aqui uma mensalidade ainda sem pagamento confirmado (boleto final 4821). Vou passar para alguém "
        "da nossa equipe, que vai te responder por aqui. Fique tranquilo: nenhuma decisão sobre o seu plano "
        "foi tomada. (dados de 30/09)"
    )
    assert motivo_de_recusa(texto, "ack_escalacao", None) is None


def test_segunda_via() -> None:
    fatos = _fatos(_OUTUBRO)
    texto = texto_segunda_via(_estado(_OUTUBRO), fatos, _HOJE)
    assert texto == (
        "Você consegue a 2ª via pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de "
        "atendimento. A mensalidade de outubro de 2026 já está paga. (dados de 08/10)"
    )
    assert _cerca(texto, fatos) is None


# --- 2. Substancia, em qualquer texto do Lucas ao beneficiario -----------------------------------------


def _todos_os_textos() -> dict[str, str]:
    estado = _estado(_OUTUBRO, _JULHO)
    textos: dict[str, str] = {
        "RESPOSTA_INFORMATIVA_RECUSADA": RESPOSTA_INFORMATIVA_RECUSADA,
        "VENCIMENTO_NAO_ENCONTRADO": VENCIMENTO_NAO_ENCONTRADO,
        "SEGUNDA_VIA_CANAIS": SEGUNDA_VIA_CANAIS,
        "ACK_ESCALACAO": ACK_ESCALACAO,
        "ACK_ESCALACAO_RECUSADO": ACK_ESCALACAO_RECUSADO,
        "ACK_ATENDIMENTO_JA_ABERTO": ACK_ATENDIMENTO_JA_ABERTO,
        "em_dia": texto_mensalidade_em_dia(estado, _HOJE),
        "vencimento": texto_vencimento(estado, _fatos(_OUTUBRO), _HOJE),
        "vencimento_aberto": texto_vencimento(_estado(_NOVEMBRO), _fatos(_NOVEMBRO), _HOJE),
        "segunda_via": texto_segunda_via(estado, _fatos(_OUTUBRO), _HOJE),
        "segunda_via_aberto": texto_segunda_via(_estado(_NOVEMBRO), _fatos(_NOVEMBRO), _HOJE),
        "valores_nao_encontrado": texto_valores(estado, _fatos(_OUTUBRO, pedida="2025-01"), _HOJE),
    }
    for foco in ("valor", "coparticipacao", "saldo", "pagamento", "vencimento", "geral"):
        for nome, c in (("paga", _OUTUBRO), ("aberto", _NOVEMBRO)):
            textos[f"valores_{foco}_{nome}"] = texto_valores(
                _estado(c), _fatos(c, pedida=c["competencia"], foco=foco), _HOJE
            )
    # 12/10/2026: as respostas em LISTA (o nome nao comeca com `valores_`: a lista cita um valor por mes).
    for foco in ("geral", "coparticipacao", "saldo", "vencimento"):
        for nome, lista, achadas, esperadas in (
            ("completa", PedidoDeLista(2), 2, 2),
            ("faltando", PedidoDeLista(3, situacao="paga"), 2, 3),
            ("ano", PedidoDeLista(12, de="2026-01", ate="2026-12"), 2, None),
        ):
            fatos_da_lista = _fatos(
                None,
                foco=foco,
                lista=lista.como_handoff(),
                lista_encontradas=achadas,
                lista_esperadas=esperadas,
            )
            fatos_da_lista["competencias"] = [_OUTUBRO, _JULHO][:achadas]
            textos[f"lista_{foco}_{nome}"] = texto_valores(estado, fatos_da_lista, _HOJE)
    textos["lista_nenhuma"] = texto_valores(
        estado,
        _fatos(
            _OUTUBRO, lista=PedidoDeLista(3, de="2024-01", ate="2024-03").como_handoff(), lista_encontradas=0
        ),
        _HOJE,
    )
    textos["um_mes_por_vez"] = f"{UM_MES_POR_VEZ} {texto_segunda_via(estado, _fatos(_OUTUBRO), _HOJE)}"
    for motivo in ("ambiguidade", "inadimplencia_detectada", "pedido_cancelamento", "contestacao_cobranca"):
        for conciliado in (True, False, None):
            estado_ack = cast(
                LucasState,
                {
                    "motivo_humano": motivo,
                    "status_conciliado": conciliado,
                    "numero_boleto": "****4821",
                    "cnab_ref": "amh-billing:2026-09-30",
                },
            )
            textos[f"ack_{motivo}_{conciliado}"] = texto_ack_escalacao(estado_ack, _HOJE)
    # 13/10/2026 (simulador): o ACK de atraso com o fato, o caso ja' aberto (as duas variantes, com e sem
    # fato) e a 2a via pedida de novo.
    vencida = {**_JULHO, "situacao": "vencida", "valor_saldo": "9248.38"}
    vencida.pop("liquidado_em")
    atraso = _estado(_NOVEMBRO, vencida, motivo_humano="inadimplencia_detectada", numero_boleto="****0042")
    textos["ack_atraso_com_fato"] = texto_ack_escalacao(atraso, _HOJE)
    for variante in (0, 1):
        com_fato = _estado(_NOVEMBRO, _OUTUBRO, variante_de_redacao=variante)
        textos[f"ack_caso_aberto_com_fato_{variante}"] = texto_caso_aberto(com_fato, _HOJE)
        contestacao = _estado(_OUTUBRO, variante_de_redacao=variante, contesta_cobranca=True)
        textos[f"ack_caso_aberto_contestacao_{variante}"] = texto_caso_aberto(contestacao, _HOJE)
        repetida = _estado(_NOVEMBRO, variante_de_redacao=variante)
        textos[f"segunda_via_repetida_{variante}"] = texto_segunda_via_repetida(
            repetida, _fatos(_NOVEMBRO), _HOJE
        )
    # Rodada 5 (13/10/2026): extrato, pagamento ao prestador e a reclamacao com os fatos do mes.
    for nome in ("TEXTO_EXTRATO", "TEXTO_EXTRATO_CURTO", "TEXTO_PAGAMENTO_AO_PRESTADOR"):
        textos[nome.lower()] = getattr(lg, nome)
    textos["texto_pagamento_ao_prestador_curto"] = lg.TEXTO_PAGAMENTO_AO_PRESTADOR_CURTO
    for avisos in (0, 1):
        reclamacao_r5 = _estado(
            _OUTUBRO,
            avisos_de_caso_aberto=avisos,
            competencia="2026-10",
            cobranca_reclamacao="em_dobro",
        )
        # So' a primeira (com a garantia) e' ACK de escalacao; as curtas seguintes, nao.
        nome_r5 = f"ack_reclamacao_r5_{avisos}" if avisos == 0 else f"reclamacao_curta_r5_{avisos}"
        textos[nome_r5] = lg.texto_da_reclamacao(reclamacao_r5, _HOJE)
    # Rodada 4 (13/10/2026): a escada sem repeticao, a empresa e a reclamacao com o caso aberto.
    textos["nao_consigo_com_oferta"] = lg.NAO_CONSIGO_COM_OFERTA
    for i, frase in enumerate(lg.NAO_CONSIGO):
        textos[f"nao_consigo_{i}"] = frase
    empresa = _estado(_OUTUBRO, competencia="2026-07", pagador_tipo="pessoa_juridica")
    textos["empresa_explicacao"] = lg.texto_pagador_empresa(empresa, {"foco": None}, _HOJE)
    textos["empresa_curta"] = lg.texto_pagador_empresa_curto(empresa, _HOJE)
    for variante in (0, 1):
        reclamacao = _estado(
            _OUTUBRO, variante_de_redacao=variante, competencia="2026-09", cobranca_reclamacao="em_dobro"
        )
        textos[f"ack_reclamacao_{variante}"] = lg.texto_da_reclamacao(reclamacao, _HOJE)
    # Rodada 3 (13/10/2026): mes perguntado, confirmacao da mais recente, esclarecimento e status com mes
    # em aberto (sem "em dia").
    textos["status_mes_perguntado"] = texto_mensalidade_em_dia(
        _estado(_OUTUBRO, _JULHO, competencia="2026-07"), _HOJE
    )
    textos["status_com_mes_em_aberto"] = texto_mensalidade_em_dia(_estado(_NOVEMBRO, _OUTUBRO), _HOJE)
    textos["ultima"] = str(texto_da_ultima(_estado(_NOVEMBRO, _OUTUBRO, competencia="2026-12"), _HOJE))
    textos["esclarecimento"] = str(texto_de_esclarecimento(_estado(_OUTUBRO, competencia="2026-10"), _HOJE))
    return textos


TEXTOS = _todos_os_textos()
#: Os textos de escalacao/atraso: a garantia na forma unica e nunca emoji.
ESCALACAO = {nome for nome in TEXTOS if nome.startswith("ack_") or nome.startswith("ACK_")}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


def _emojis(texto: str) -> list[str]:
    return [c for c in texto if unicodedata.category(c) == "So"]


def test_a_colecao_nao_e_vacua() -> None:
    assert len(TEXTOS) >= 30 and len(ESCALACAO) >= 12


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_nenhum_canal_proibido(nome: str) -> None:
    plano = _sem_acento(TEXTOS[nome])
    for proibido in ("portal do beneficiario", "portal da operadora", "aplicativo do plano", "0800", "site"):
        assert proibido not in plano, f"{nome} cita {proibido!r}"


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_no_maximo_um_emoji_e_nunca_em_escalacao(nome: str) -> None:
    emojis = _emojis(TEXTOS[nome])
    assert len(emojis) <= 1
    if nome in ESCALACAO:
        assert emojis == []


@pytest.mark.parametrize("nome", sorted(ESCALACAO))
def test_escalacao_tem_a_garantia_exata(nome: str) -> None:
    assert GARANTIA_SEM_DECISAO == "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada."
    assert GARANTIA_SEM_DECISAO in TEXTOS[nome]
    assert "nada foi alterado" not in _sem_acento(TEXTOS[nome])
    assert "inadimpl" not in _sem_acento(TEXTOS[nome])


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_garantia_so_na_forma_unica(nome: str) -> None:
    if "nenhuma decisao" in _sem_acento(TEXTOS[nome]):
        assert GARANTIA_SEM_DECISAO in TEXTOS[nome]


@pytest.mark.parametrize("nome", sorted(TEXTOS))
def test_frases_curtas_e_sem_saudacao(nome: str) -> None:
    texto = TEXTOS[nome]
    assert not _sem_acento(texto).startswith(("ola", "oi"))
    # "R$ 9.248,38" tem ponto de milhar: tira-se o numero antes de partir em frases.
    sem_numeros = "".join("0" if ch.isdigit() else ch for ch in texto).replace("0.0", "00")
    for frase in sem_numeros.replace("?", ".").replace("!", ".").replace(":", ".").split("."):
        assert len(frase.split()) <= 20, f"{nome}: frase longa demais: {frase.strip()!r}"


@pytest.mark.parametrize("nome", sorted(n for n in TEXTOS if n.startswith("valores_")))
def test_a_resposta_de_valor_fala_so_de_uma_coisa(nome: str) -> None:
    """Responder SO' o que foi perguntado: nenhuma resposta de valor e' extrato."""
    texto = _sem_acento(TEXTOS[nome])
    assert "competencia" not in texto and "consultei" not in texto and "outro mes" not in texto
    assert texto.count("r$") <= 1


def test_nenhuma_constante_de_texto_do_grafo_cita_canal_proibido() -> None:
    """Varredura das constantes `str` do modulo (pega texto novo que ninguem lembrou de por na lista)."""
    for nome, valor in vars(lg).items():
        if nome.isupper() and isinstance(valor, str):
            assert "portal do beneficiario" not in _sem_acento(valor), nome


def test_o_prompt_do_lembrete_traz_o_tom_sem_tirar_as_proibicoes() -> None:
    texto = message_prompt()
    assert "TOM DA CONVERSA" in texto and "SO o que foi perguntado" in texto
    assert "a proibicao vence" in texto
    assert "NUNCA comunique uma negativa, suspensao ou cancelamento" in texto


# --- 3. As cercas aceitam as formas novas e continuam recusando o que nao esta' nos fatos -------------


@pytest.mark.parametrize(
    ("texto", "grupo"),
    [
        (
            "Sua mensalidade de julho foi de R$ 9.248,39 e já está paga. (dados de 08/10)",
            RECUSA_VALOR_SEM_FATO,
        ),
        (
            "Sua mensalidade de julho foi de R$ 9.248,38 e já está paga (você pagou em 28/07).",
            RECUSA_DATA_SEM_FATO,
        ),
        ("A de julho vence em 26/07, mas você já pagou.", RECUSA_DATA_SEM_FATO),
        ("Sua mensalidade de julho já está paga. (dados de 09/10)", RECUSA_DATA_SEM_FATO),
        ("A mensalidade de julho (boleto final 9999) já está paga.", RECUSA_BOLETO_SEM_FATO),
    ],
)
def test_a_cerca_continua_recusando_o_inventado(texto: str, grupo: str) -> None:
    recusa = _cerca(texto, _fatos(_JULHO, pedida="2026-07"))
    assert recusa is not None and recusa[0] == grupo, texto


@pytest.mark.parametrize("nome", sorted(n for n in TEXTOS if not n.startswith(("ack_", "ACK_"))))
def test_todo_texto_informativo_passa_na_cerca_com_os_fatos_dele(nome: str) -> None:
    fatos = {"competencias": [_OUTUBRO, _JULHO, _NOVEMBRO], "valor_em_aberto": "0.00"}
    assert _cerca(TEXTOS[nome], fatos) is None, TEXTOS[nome]
