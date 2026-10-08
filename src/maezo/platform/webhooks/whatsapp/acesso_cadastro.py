"""Respostas FIXAS sobre o cadastro, para a conversa VERIFICADA (DL-0083, item 8 da decisao do dono).

Com o acesso do beneficiario ligado e a conversa em `verificado`, a Helena pode mostrar dados do plano que a
AMH
ja' devolve no perfil (`get_subject_profile`: plano ativo, vigencia inicio/fim, carencia vigente, faixa
etaria), SO' para perguntas EXPLICITAS (lexicais) e SO' por modelos de texto fixo. Nenhum LLM toca no dado:
nao
ha' prompt, nao ha' redacao, nao ha' extracao; a mensagem que casa nunca chega ao grafo. Nunca nome, CPF,
telefone, idade exata, nascimento ou titular: so' o vocabulario fechado da identidade da Helena
(`graph.IDENTIDADE_CHAVES`), que ja' e' grosso (faixa) e pseudonimo.

Tambem aqui: "sabe quem sou eu?" com a conversa verificada -> frase fixa SEM dado nenhum.

A deteccao e' lexical, sobre o texto normalizado (sem acento, caixa baixa, pontuacao como espaco), e e'
propositalmente
ESTREITA: pergunta que mistura cobranca, saude ou pedido de pessoa segue para o grafo (que decide). Quando o
perfil nao esta' disponivel, a resposta e' a frase fixa de indisponibilidade (nunca um chute).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from maezo.agents.helena.graph import eh_pergunta_quem_sou_eu

from .pre_roteamento import normalizar

TIPO_QUEM_SOU_EU: Final[str] = "quem_sou_eu"
TIPO_PLANO_ATIVO: Final[str] = "plano_ativo"
TIPO_VIGENCIA: Final[str] = "vigencia"
TIPO_CARENCIA: Final[str] = "carencia"
TIPO_FAIXA: Final[str] = "faixa_etaria"

RESPOSTA_QUEM_SOU_EU: Final[str] = "Sim: você foi identificado como beneficiário da Austa Clínicas."
RESPOSTA_CADASTRO_INDISPONIVEL: Final[str] = (
    "No momento não consegui consultar o seu cadastro. Tente novamente em alguns minutos."
)
RESPOSTA_SEM_INFORMACAO: Final[str] = "Não encontrei essa informação no seu cadastro."

_FAIXAS: Final[dict[str, str]] = {
    "lactente": "lactente (menos de 2 anos)",
    "crianca": "criança (2 a 11 anos)",
    "adolescente": "adolescente (12 a 17 anos)",
    "adulto": "adulto (18 a 59 anos)",
    "idoso": "idoso (60 anos ou mais)",
}

# Assuntos que a pergunta de cadastro NAO pode misturar: com eles quem decide e' o grafo
# (cobranca/saude/pessoa).
_MISTURA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:boletos?|faturas?|cobrancas?|mensalidades?|pagamentos?|pagar|paguei|pix|debito|vencimento|reembolso"
    r"|atendente|humano|pessoa|dor|febre|sintoma\w*|sangr\w+|emergencia|urgencia)\b"
)
_PLANO_ATIVO: Final[re.Pattern[str]] = re.compile(
    r"\bplano\b.{0,30}\b(?:ativo|ativa|vigente|valido|valida|cancelado|cancelada|em dia)\b"
    r"|\b(?:ativo|ativa|vigente)\b.{0,20}\bplano\b"
)
_VIGENCIA: Final[re.Pattern[str]] = re.compile(
    r"\bvigencia\b|\bate quando\b.{0,30}\b(?:plano|vale|valido|vai)\b"
)
_CARENCIA: Final[re.Pattern[str]] = re.compile(r"\bcarencias?\b")
_FAIXA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:qual|quais)\b.{0,15}\b(?:minha|a minha)\b.{0,10}\b(?:faixa|idade)\b"
    r"|\bminha faixa etaria\b|\bfaixa etaria\b.{0,15}\b(?:minha|meu cadastro|cadastro)\b"
)


def tipo_de_pergunta(texto: str) -> str | None:
    """O tipo fechado da pergunta de cadastro/identidade, ou `None` (a mensagem segue para o grafo)."""
    plano = normalizar(texto or "")
    if not plano:
        return None
    if eh_pergunta_quem_sou_eu(texto) and not _MISTURA.search(plano):
        return TIPO_QUEM_SOU_EU
    if _MISTURA.search(plano):
        return None
    if _VIGENCIA.search(plano):
        return TIPO_VIGENCIA
    if _CARENCIA.search(plano):
        return TIPO_CARENCIA
    if _FAIXA.search(plano):
        return TIPO_FAIXA
    if _PLANO_ATIVO.search(plano):
        return TIPO_PLANO_ATIVO
    return None


def _data_br(valor: object) -> str | None:
    """`YYYY-MM-DD` -> `dd/mm/aaaa`; qualquer outra coisa e' `None` (nunca se completa um fato ausente)."""
    if not isinstance(valor, str):
        return None
    try:
        data = date.fromisoformat(valor)
    except ValueError:
        return None
    return data.strftime("%d/%m/%Y")


def _vigencia(identidade: Mapping[str, Any]) -> str | None:
    inicio = _data_br(identidade.get("vigencia_inicio"))
    fim = _data_br(identidade.get("vigencia_fim"))
    if inicio and fim:
        return f"com vigência de {inicio} até {fim}"
    if inicio:
        return f"com vigência desde {inicio}"
    return None


def resposta_de_cadastro(tipo: str, identidade: Mapping[str, Any] | None) -> str:
    """O texto FIXO da pergunta `tipo`, montado so' com o vocabulario fechado da identidade.

    `identidade` e' o dict de `normalizar_identidade` (ou `None` quando o perfil nao veio): `None` -> frase de
    indisponibilidade. Nunca levanta e nunca devolve dado fora do vocabulario fechado.
    """
    if tipo == TIPO_QUEM_SOU_EU:
        return RESPOSTA_QUEM_SOU_EU
    if identidade is None:
        return RESPOSTA_CADASTRO_INDISPONIVEL
    if tipo == TIPO_PLANO_ATIVO:
        ativo = identidade.get("plano_ativo")
        if ativo is True:
            vigencia = _vigencia(identidade)
            return f"Seu plano está ativo, {vigencia}." if vigencia else "Seu plano está ativo."
        if ativo is False:
            return "Seu plano não consta como ativo no cadastro."
        return RESPOSTA_SEM_INFORMACAO
    if tipo == TIPO_VIGENCIA:
        vigencia = _vigencia(identidade)
        return f"O seu plano consta {vigencia}." if vigencia else RESPOSTA_SEM_INFORMACAO
    if tipo == TIPO_CARENCIA:
        carencia = identidade.get("carencia_vigente")
        if carencia is True:
            return "Consta no seu cadastro que há carência vigente."
        if carencia is False:
            return "Consta no seu cadastro que não há carência vigente."
        return RESPOSTA_SEM_INFORMACAO
    if tipo == TIPO_FAIXA:
        faixa = _FAIXAS.get(str(identidade.get("faixa_etaria")))
        return f"A sua faixa etária no cadastro é: {faixa}." if faixa else RESPOSTA_SEM_INFORMACAO
    return RESPOSTA_SEM_INFORMACAO


__all__ = [
    "RESPOSTA_CADASTRO_INDISPONIVEL",
    "RESPOSTA_QUEM_SOU_EU",
    "RESPOSTA_SEM_INFORMACAO",
    "TIPO_CARENCIA",
    "TIPO_FAIXA",
    "TIPO_PLANO_ATIVO",
    "TIPO_QUEM_SOU_EU",
    "TIPO_VIGENCIA",
    "resposta_de_cadastro",
    "tipo_de_pergunta",
]
