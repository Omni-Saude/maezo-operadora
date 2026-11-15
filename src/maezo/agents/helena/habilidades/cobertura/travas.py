"""As TRAVAS DE POLITICA da habilidade `cobertura`, no ESQUELETO e nao em cada camada (DL-0167, HEL-1).

O texto que uma camada deixa pronto sai sem o modelo de redacao (o `inform` o envia literal), e por isso nao
passa pelas travas do `inform`. Elas sao aplicadas AQUI, a todo texto de `resposta` e de `esclarecer`, antes
de o texto ser aceito; qualquer reprovacao = o aviso de canais (falha fechada). O verificador de FATOS da
camada (fatos x fontes) e' outra coisa e nao substitui estas travas.

1. as cercas de saida de sempre (canal nao confirmado, recusa de saida: `graph._cercar_saida`);
2. sem conduta clinica (`graph._conduta_clinica`), sem negar acesso (`graph._nega_acesso`), sem promessa de
   repasse (`graph._PROMESSA_DE_REPASSE`);
3. `cobertura_sem_promessa` na forma da regra geral: nunca prometer nem negar cobertura AO CASO da pessoa e
   nunca avaliar se ela cumpre um criterio (`DECISAO_DO_CASO`); a `resposta` termina com a formula fixa
   "informacao nao e' decisao" (`FORMULA_FIXA`); o `esclarecer` nao afirma cobertura nenhuma
   (`_AFIRMA_COBERTURA`);
4. as travas do `respond` (`travas_saida.na_saida`: link, contagem, prazo da ANS, reembolso, midia): texto que
   elas mudariam e' recusado aqui, para nunca sair cortado nem vazio.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

#: A formula fixa (guia de tom: "nao confirmam cobertura nem autorizacao"), na forma da regra geral.
FORMULA_FIXA: Final[str] = (
    "Essa é a regra geral e não confirma a cobertura nem a autorização do seu pedido: quem decide é a "
    "análise do plano."
)
#: Promessa ou negativa de cobertura ao CASO da pessoa, ou avaliacao de criterio dela (texto normalizado).
DECISAO_DO_CASO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:seu|o seu|teu) plano (?:cobre|vai cobrir|cobrira|nao cobre|paga|vai pagar|autoriza|nao autoriza)\b"
    r"|\b(?:voce|vc) (?:tem|tera|vai ter|nao tem|nao tera) (?:cobertura|direito)\b"
    r"|\b(?:voce|vc) (?:cumpre|atende|preenche|se enquadra|nao cumpre|nao atende|nao se enquadra)\b"
    r"|\b(?:vai|sera|vao|serao) (?:ser )?(?:autorizad|aprovad|liberad|negad|recusad|cobert)\w*"
    r"|\b(?:esta|fica|ficou) (?:garantid|aprovad|liberad|autorizad)\w*"
    r"|\bcobertura (?:e|esta) garantida\b|\bpode fazer sem\b|\bpode ficar tranquil\w*"
    # revisao independente (HEL-3b): outras formas de decidir o caso
    r"|\b(?:seu|o seu|teu) plano (?:libera|vai liberar|aprova|vai aprovar|nao libera)\b"
    r"|\b(?:e|esta|sera|fica|ficou) (?:cobert[oa]s?|liberad[oa]s?|aprovad[oa]s?) (?:pelo|no) (?:seu )?plano\b"
    r"|\b(?:seu|o seu) caso (?:se encaixa|se enquadra|cumpre|atende|nao se encaixa|nao se enquadra)\b"
    r"|\bpode (?:marcar|agendar|fazer|ir) (?:direto|sem)\b|\b(?:voce|vc) pode fazer\b"
)

Motivo = Literal[
    "cerca_de_saida",
    "conduta_clinica",
    "nega_acesso",
    "promessa_de_repasse",
    "decisao_do_caso",
    "sem_formula_fixa",
    "afirma_cobertura",
    "trava_de_saida",
]


def motivo_de_recusa(grafo: HelenaGraph, texto: str, *, esclarecer: bool) -> Motivo | None:
    """`None` = o texto pode sair; senao, o motivo (codigo curto, nunca o texto)."""
    from maezo.agents.helena import travas_saida as _travas_saida
    from maezo.agents.helena.graph import (
        _AFIRMA_COBERTURA,
        _PROMESSA_DE_REPASSE,
        RespostaRecusadaError,
        _conduta_clinica,
        _nega_acesso,
        _plano,
    )

    try:
        grafo._cercar_saida(texto, "inform", node="cobertura")
    except RespostaRecusadaError:
        return "cerca_de_saida"
    if _conduta_clinica(texto):
        return "conduta_clinica"
    if _nega_acesso(texto):
        return "nega_acesso"
    plano = _plano(texto)
    if _PROMESSA_DE_REPASSE.search(plano):
        return "promessa_de_repasse"
    corpo = texto[: -len(FORMULA_FIXA)] if texto.endswith(FORMULA_FIXA) else texto
    if DECISAO_DO_CASO.search(_plano(corpo)):
        return "decisao_do_caso"
    if esclarecer and _AFIRMA_COBERTURA.search(plano):
        return "afirma_cobertura"
    if not esclarecer and not texto.endswith(FORMULA_FIXA):
        return "sem_formula_fixa"
    if _travas_saida.na_saida(texto) != texto:
        return "trava_de_saida"
    return None


__all__ = ["DECISAO_DO_CASO", "FORMULA_FIXA", "Motivo", "motivo_de_recusa"]
