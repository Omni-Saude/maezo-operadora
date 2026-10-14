"""Textos FIXOS do lembrete proativo de cobranca (guia de tom, `docs/guia-de-tom-dos-agentes.md`).

Nada aqui e' gerado por modelo: o lembrete e' uma frase com tres fatos (mes e ano da competencia, valor,
vencimento), a orientacao de 2a via e a saida (PARAR). Sem nome de agente, mes SEMPRE com ano, frases de
ate' 20 palavras, no maximo um emoji (nenhum). A mesma frase, com as mesmas tres variaveis, e' o corpo do
TEMPLATE que o dono submete na Meta (`docs/runbooks/lembrete-cobranca.md`): texto livre dentro da janela de
24 h e template fora dela saem IGUAIS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

MESES: Final[tuple[str, ...]] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

FRASE_LEMBRETE: Final[str] = "Sua mensalidade de {mes_ano} (R$ {valor}) vence em {vencimento}."
SEGUNDA_VIA: Final[str] = (
    "Para a 2ª via, acesse o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento."
)
SAIDA_PARAR: Final[str] = "Para não receber lembretes, responda PARAR."
CONFIRMACAO_PARAR: Final[str] = "Você não receberá mais lembretes."
MARCA_TESTE: Final[str] = "[TESTE]"

#: Corpo do template da Meta, com as variaveis `{{1}}` mes e ano, `{{2}}` valor, `{{3}}` vencimento.
CORPO_TEMPLATE_META: Final[str] = (
    "Sua mensalidade de {{1}} (R$ {{2}}) vence em {{3}}. " + SEGUNDA_VIA + " " + SAIDA_PARAR
)


@dataclass(frozen=True, slots=True)
class MensagemLembrete:
    """O que sai: o texto livre completo e as tres variaveis do template (na ordem `{{1}}..{{3}}`)."""

    texto: str
    parametros_template: tuple[str, str, str]


def mes_e_ano(competencia: str) -> str:
    """`2026-10` -> `outubro de 2026` (o mes NUNCA vai sem o ano)."""
    ano, mes = competencia.split("-")
    return f"{MESES[int(mes) - 1]} de {int(ano)}"


def valor_em_reais(decimal: str) -> str:
    """`320.25` -> `320,25` (so' a forma do contrato `0.00`; o chamador ja' validou)."""
    inteiro, centavos = decimal.split(".")
    return f"{inteiro},{centavos}"


def dia_e_mes(data_iso: date) -> str:
    """`2026-10-20` -> `20/10`."""
    return f"{data_iso.day:02d}/{data_iso.month:02d}"


def compor_lembrete(*, competencia: str, valor_total: str, vencimento: date) -> MensagemLembrete:
    mes_ano = mes_e_ano(competencia)
    valor = valor_em_reais(valor_total)
    venc = dia_e_mes(vencimento)
    frase = FRASE_LEMBRETE.format(mes_ano=mes_ano, valor=valor, vencimento=venc)
    return MensagemLembrete(
        texto=f"{frase} {SEGUNDA_VIA} {SAIDA_PARAR}",
        parametros_template=(mes_ano, valor, venc),
    )


def compor_teste(*, competencia: str, valor_total: str, vencimento: date) -> MensagemLembrete:
    """O lembrete SINTETICO do modo de teste: o mesmo texto, claramente marcado. No template, a marca vai
    na variavel 1 (`[TESTE] outubro de 2026`), porque o corpo aprovado na Meta nao muda."""
    base = compor_lembrete(competencia=competencia, valor_total=valor_total, vencimento=vencimento)
    mes_ano, valor, venc = base.parametros_template
    return MensagemLembrete(
        texto=f"{MARCA_TESTE} {base.texto}",
        parametros_template=(f"{MARCA_TESTE} {mes_ano}", valor, venc),
    )
