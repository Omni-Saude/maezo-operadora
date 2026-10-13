"""Teste REAL do dono no WhatsApp de dev (rodada 8, 13/10/2026), com os dados reais dele.

Dados: 12 competencias 2025-11..2026-10, TODAS `paga`, valor R$ 0,00, coparticipacao 0,00, saldo 0,00,
`pagador_tipo=pessoa_juridica`, ciclos 0 (a ultima, 2026-10, vence 25/10 e foi paga em 06/10).

Defeito: a escada de repeticao do contrato de empresa contava respostas de ASSUNTOS diferentes como a mesma
pergunta (a chave era so' o mes). "Preciso da 2a via do boleto" recebia a forma curta do PAGAMENTO e "Quando
vence o boleto?" recebia "Nao tenho outra informacao sobre isso" na primeira vez.

Tudo contra o grafo REAL do Lucas, a DMN DRAFT e o motor em memoria (o `_Conversa` da rodada 4).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    AVISOS_DE_CASO_ABERTO,
    NAO_CONSIGO,
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
    # d) "Preciso da 2a via do boleto"
    _, d = await conversa.mensagem(tipo="2a_via", intencao="cobranca_info")
    assert d == TEXTO_EMPRESA_SEGUNDA_VIA
    assert "confirmação do pagamento" not in d
    # e) "Quando vence o boleto? E meu plano cobre dermatologista?"
    _, e = await conversa.mensagem(tipo="vencimento", intencao="cobranca_info", cobranca_foco="vencimento")
    assert e == TEXTO_EMPRESA_VENCIMENTO
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
    # Pergunta 1: completa. Repeticoes reais: a curta, "nao consigo detalhar" e so' entao as honestas.
    assert textos[0] not in NAO_CONSIGO
    assert textos[1] not in NAO_CONSIGO and len(textos[1]) < len(textos[0])
    assert textos[2] == NAO_CONSIGO[1]
    assert all("Não tenho outra informação" not in t for t in textos[:3])
    assert textos[3] == NAO_CONSIGO[0] and textos[4] == NAO_CONSIGO[1]
    _sem_repeticao(textos)


async def test_assunto_diferente_zera_a_escada_mesmo_depois_de_repeticoes() -> None:
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
    assert vencimento == TEXTO_EMPRESA_VENCIMENTO
    _, segunda_via = await conversa.mensagem(tipo="2a_via", intencao="cobranca_info")
    assert segunda_via == TEXTO_EMPRESA_SEGUNDA_VIA
    _, pagamento_de_novo = await conversa.mensagem(**pagamento, competencia="2026-07")
    assert pagamento_de_novo == _EXPLICACAO_JULHO


async def test_o_mes_diferente_do_mesmo_assunto_tambem_e_outra_pergunta() -> None:
    conversa = _Conversa(_DONO)
    pagamento = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "cobranca_foco": "pagamento",
    }
    _, julho = await conversa.mensagem(**pagamento, competencia="2026-07")
    _, agosto = await conversa.mensagem(**pagamento, competencia="2026-08")
    assert "julho de 2026" in julho and "agosto de 2026" in agosto
    assert agosto == julho.replace("julho", "agosto")


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
