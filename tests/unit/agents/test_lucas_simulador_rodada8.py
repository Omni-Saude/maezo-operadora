"""Teste REAL do dono no WhatsApp de dev (rodada 8, 13/10/2026), com os dados reais dele.

Dados: 12 competencias 2025-11..2026-10, TODAS `paga`, valor R$ 0,00, coparticipacao 0,00, saldo 0,00,
`pagador_tipo=pessoa_juridica`, ciclos 0 (a ultima, 2026-10, vence 25/10 e foi paga em 06/10).

Defeito: a escada de repeticao do contrato de empresa contava respostas de ASSUNTOS diferentes como a mesma
pergunta (a chave era so' o mes). "Preciso da 2a via do boleto" recebia a forma curta do PAGAMENTO e "Quando
vence o boleto?" recebia "Nao tenho outra informacao sobre isso" na primeira vez.

Tudo contra o grafo REAL do Lucas, a DMN DRAFT e o motor em memoria (o `_Conversa` da rodada 4).

FASE 4 (18/10/2026): o contrario tambem era defeito — trocar de assunto ou de mes logo depois de um texto da
empresa repetia "Sua mensalidade e' paga pela empresa contratante." inteira. Agora a explicacao inteira so'
sai quando a resposta anterior do Lucas nao foi da empresa; depois, a curta do assunto da pergunta, que se
reveza, e a oferta de uma pessoa no maximo uma vez a cada duas respostas.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    AVISOS_DE_CASO_ABERTO,
    NAO_CONSIGO,
    OFERTA_PESSOA,
    TEXTO_EMPRESA_SEGUNDA_VIA,
    TEXTO_EMPRESA_VENCIMENTO,
    TEXTO_PAGADOR_EMPRESA,
)
from maezo.tools.mcp_cibseven.transport import ProcessInstance
from tests.unit.agents.test_lucas_simulador_rodada4 import _Conversa


def _competencias_do_dono() -> list[dict[str, str]]:
    meses = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
    return [
        {
            "competencia": mes,
            "situacao": "paga",
            "vencimento": f"{mes}-25",
            "valor_total": "0.00",
            "valor_coparticipacao": "0.00",
            "valor_saldo": "0.00",
            "liquidado_em": f"{mes}-06",
        }
        for mes in reversed(meses)
    ]


_DONO: dict[str, Any] = {
    "status_conciliado": True,
    "ciclos_sem_conciliacao": 0,
    "numero_boleto": "",
    "valor_em_aberto": "0.00",
    "dias_atraso_max": 0,
    "vencimento_referencia": "2026-10-25",
    "competencias_cobranca": _competencias_do_dono(),
    "pagador_tipo": "pessoa_juridica",
}

_EXPLICACAO_JULHO = (
    f"{TEXTO_PAGADOR_EMPRESA} Por isso, a situação do pagamento de julho de 2026 fica com a empresa "
    "(normalmente o RH). Se quiser, posso te passar para alguém da nossa equipe."
)


def _sem_repeticao(textos: list[str]) -> None:
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos
    # FASE 4: a oferta de uma pessoa nunca em duas respostas seguidas.
    ofertas = ["nossa equipe" in t for t in textos]
    assert not any(a and b for a, b in zip(ofertas, ofertas[1:], strict=False)), textos
    # FASE 4: a abertura da empresa nunca em duas respostas seguidas.
    aberturas = [t.startswith(TEXTO_PAGADOR_EMPRESA) for t in textos]
    assert not any(a and b for a, b in zip(aberturas, aberturas[1:], strict=False)), textos


async def test_a_ate_e_do_dono_cada_pergunta_recebe_a_resposta_do_seu_assunto() -> None:
    conversa = _Conversa(_DONO)
    # a) "A mensalidade de julho foi paga?"
    _, a = await conversa.mensagem(
        tipo="status_pagamento",
        intencao="confirmacao_pagamento",
        competencia="2026-07",
        cobranca_foco="pagamento",
    )
    assert a == _EXPLICACAO_JULHO
    # b) "Quanto foi minha coparticipacao em setembro?"
    _, b = await conversa.mensagem(competencia="2026-09", cobranca_foco="coparticipacao")
    assert b == "Em setembro de 2026 você não teve coparticipação: R$ 0,00. (dados de 12/10)"
    # c) "Quais foram as ultimas tres mensalidades pagas por mim?"
    lista = {"quantidade": 3, "de": None, "ate": None, "situacao": "paga"}
    _, c = await conversa.mensagem(cobranca_lista=lista)
    assert c.startswith(TEXTO_PAGADOR_EMPRESA) and "Por isso, a situação do pagamento" in c
    # d) "Preciso da 2a via do boleto" — logo depois de um texto da empresa: a curta da 2a via, sem oferta
    # (a resposta anterior ja' ofereceu).
    _, d = await conversa.mensagem(tipo="2a_via", intencao="cobranca_info")
    assert d == "A 2ª via é com a empresa contratante, normalmente pelo RH. Por aqui eu não envio o boleto."
    assert "confirmação do pagamento" not in d
    # e) "Quando vence o boleto? E meu plano cobre dermatologista?" — a curta do vencimento, com a oferta.
    _, e = await conversa.mensagem(tipo="vencimento", intencao="cobranca_info", cobranca_foco="vencimento")
    assert e == f"O vencimento e o boleto são com a empresa contratante, normalmente pelo RH. {OFERTA_PESSOA}"
    assert not any("Não tenho outra informação" in t for t in conversa.textos)
    _sem_repeticao(conversa.textos)


@pytest.mark.parametrize(
    ("entrada", "primeira"),
    [
        (
            {"tipo": "status_pagamento", "intencao": "confirmacao_pagamento", "cobranca_foco": "pagamento"},
            None,
        ),
        ({"tipo": "2a_via", "intencao": "cobranca_info"}, TEXTO_EMPRESA_SEGUNDA_VIA),
        (
            {"tipo": "vencimento", "intencao": "cobranca_info", "cobranca_foco": "vencimento"},
            TEXTO_EMPRESA_VENCIMENTO,
        ),
    ],
    ids=["pagamento", "segunda_via", "vencimento"],
)
async def test_a_escada_so_anda_com_repeticao_real_do_mesmo_assunto(
    entrada: dict[str, Any], primeira: str | None
) -> None:
    conversa = _Conversa(_DONO)
    textos = [(await conversa.mensagem(**entrada))[1] for _ in range(6)]
    if primeira is not None:
        assert textos[0] == primeira
    # Pergunta 1: completa. Repeticoes reais (FASE 4): as tres curtas, que se revezam, e so' entao as duas
    # honestas, alternadas.
    assert textos[0] not in NAO_CONSIGO
    assert all(t not in NAO_CONSIGO and len(t) < len(textos[0]) for t in textos[1:4])
    assert len(set(textos[1:4])) == 3
    assert all("Não tenho outra informação" not in t for t in textos[:4])
    assert textos[4] == NAO_CONSIGO[1] and textos[5] == NAO_CONSIGO[0]
    _sem_repeticao(textos)


async def test_assunto_diferente_depois_da_empresa_recebe_a_curta_do_seu_assunto() -> None:
    """FASE 4: depois de um texto da empresa, a pergunta de outro assunto ouve a curta DELE (nunca a abertura
    de novo nem o "nao tenho outra informacao" do assunto anterior)."""
    conversa = _Conversa(_DONO)
    pagamento = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "cobranca_foco": "pagamento",
    }
    for _ in range(4):
        await conversa.mensagem(**pagamento, competencia="2026-07")
    _, vencimento = await conversa.mensagem(
        tipo="vencimento", intencao="cobranca_info", cobranca_foco="vencimento"
    )
    assert vencimento.startswith(
        "O vencimento e o boleto são com a empresa contratante, normalmente pelo RH."
    )
    _, segunda_via = await conversa.mensagem(tipo="2a_via", intencao="cobranca_info")
    assert segunda_via.startswith("A 2ª via é com a empresa contratante, normalmente pelo RH.")
    _, pagamento_de_novo = await conversa.mensagem(**pagamento, competencia="2026-07")
    assert pagamento_de_novo.startswith(
        "A confirmação do pagamento de julho de 2026 é com a empresa contratante"
    )
    assert not any(t in NAO_CONSIGO for t in (vencimento, segunda_via, pagamento_de_novo))
    _sem_repeticao(conversa.textos)


async def test_tres_turnos_seguidos_de_temas_diferentes_nao_repetem_a_abertura() -> None:
    """FASE 4 (o defeito do dono): pagamento, vencimento e 2a via seguidos — a explicacao inteira so' na
    primeira; as outras, curtas e diferentes, com o fato (empresa contratante / RH)."""
    conversa = _Conversa(_DONO)
    _, a = await conversa.mensagem(
        tipo="status_pagamento",
        intencao="confirmacao_pagamento",
        competencia="2026-07",
        cobranca_foco="pagamento",
    )
    _, b = await conversa.mensagem(tipo="vencimento", intencao="cobranca_info", cobranca_foco="vencimento")
    _, c = await conversa.mensagem(tipo="2a_via", intencao="cobranca_info")
    assert a == _EXPLICACAO_JULHO
    assert TEXTO_PAGADOR_EMPRESA not in b and TEXTO_PAGADOR_EMPRESA not in c
    assert b not in (TEXTO_EMPRESA_VENCIMENTO,) and c not in (TEXTO_EMPRESA_SEGUNDA_VIA,)
    assert all("RH" in t for t in (a, b, c))
    assert len({a, b, c}) == 3
    _sem_repeticao(conversa.textos)


async def test_o_mes_diferente_do_mesmo_assunto_logo_depois_recebe_a_curta() -> None:
    """FASE 4: "e agosto?" logo depois da explicacao de julho — a curta de agosto, sem a abertura de novo."""
    conversa = _Conversa(_DONO)
    pagamento = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "cobranca_foco": "pagamento",
    }
    _, julho = await conversa.mensagem(**pagamento, competencia="2026-07")
    _, agosto = await conversa.mensagem(**pagamento, competencia="2026-08")
    assert julho == _EXPLICACAO_JULHO
    assert (
        agosto
        == "A confirmação do pagamento de agosto de 2026 é com a empresa contratante, normalmente pelo RH."
    )
    _sem_repeticao(conversa.textos)


async def test_depois_de_outro_assunto_a_explicacao_volta_inteira() -> None:
    conversa = _Conversa(_DONO)
    pagamento = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "cobranca_foco": "pagamento",
    }
    await conversa.mensagem(**pagamento, competencia="2026-07")
    await conversa.mensagem(competencia="2026-09", cobranca_foco="coparticipacao")
    _, de_novo = await conversa.mensagem(**pagamento, competencia="2026-07")
    assert de_novo == _EXPLICACAO_JULHO


# --- Com o caso de cobranca aberto a empresa continua respondendo -----------------------------------------


def _abrir_caso(conversa: _Conversa) -> None:
    conversa.motor.seed_instance(
        ProcessInstance("i-8", "SP-OP-ESCALATION-001", "ESC-amh-wa:amh:hk1_1314", "ACTIVE"),
        variables={"source_agent_id": "lucas"},
    )


async def test_empresa_com_caso_aberto_responde_o_valor_de_outubro() -> None:
    conversa = _Conversa(_DONO)
    _abrir_caso(conversa)
    final, texto = await conversa.mensagem(competencia="2026-10", cobranca_foco="valor")
    assert final["caso_aberto_cobranca"] is True and final["route"] == "respond_member"
    assert texto.startswith(TEXTO_PAGADOR_EMPRESA)
    assert "Por isso, a situação do pagamento de outubro de 2026 fica com a empresa" in texto
    assert texto not in AVISOS_DE_CASO_ABERTO
    assert "R$" not in texto, "o valor do titulo da empresa nunca vai ao beneficiario"


async def test_empresa_com_caso_aberto_responde_se_agosto_foi_paga() -> None:
    conversa = _Conversa(_DONO)
    _abrir_caso(conversa)
    pagamento = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "cobranca_foco": "pagamento",
    }
    final, texto = await conversa.mensagem(**pagamento, competencia="2026-08")
    assert final["caso_aberto_cobranca"] is True
    assert "situação do pagamento de agosto de 2026 fica com a empresa" in texto
    assert texto not in AVISOS_DE_CASO_ABERTO
