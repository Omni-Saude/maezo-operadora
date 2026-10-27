"""O que a pessoa DISSE, conferido pelo codigo (rodada de 26/10/2026, imagem 7ac0219e). Modulo PURO.

1. A pre-leitura do modelo marcou "acabei torcendo o tornozelo" como "sim" para "alguma parte ficou torta ou
   muito inchada, ou voce nao consegue apoiar?" e a pessoa foi mandada ao pronto-socorro sem nenhuma
   pergunta. Um "sim" da pre-leitura so' vale quando a mensagem diz o PROPRIO sinal: uma palavra de sinal da
   pergunta ("apoiar", "torto", "inchado", "confuso", "enjoo") aparece no que a pessoa escreveu. A torcao
   ("torci") e a parte do corpo ("tornozelo", "braco") nao sao o sinal. Sem a palavra, o
   "sim" vira "nao dito" e a pergunta e' feita (o lado seguro).
2. A conferencia do especialista ("o certo nao seria ortopedista?") sem perguntas na conversa: a queixa e' a
   ULTIMA fala da pessoa que descreveu um problema (uma palavra dos exemplos do mapa), nunca o que o modelo
   escolhe da conversa inteira.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from maezo.conhecimento import base_saude as _bs
from maezo.conhecimento import protocolos as _p

_PREFIXO: Final[int] = 4

#: Palavras que nao sao sinal: gramatica, tempo, pessoa, evento do trauma e partes do corpo.
_NAO_E_SINAL: Final[frozenset[str]] = frozenset(
    p[:_PREFIXO]
    for p in (
        # gramatica / tempo / pessoa
        "alguma", "algum", "agora", "ainda", "antes", "depois", "desde", "junto", "junta", "quando",
        "como", "muito", "muita", "mais", "menos", "voce", "pessoa", "esta", "estou", "estava", "esse",
        "essa", "isso", "desses", "dessas", "tambem", "teve", "tenho", "ficou", "fiquei", "ficar", "fica",
        "parte", "corpo", "lado", "ontem", "hoje", "dias", "semana", "vezes", "pode", "para", "pela", "pelo",
        "sobre", "crianca", "bebe", "filho", "filha", "acho", "acabei", "jogava", "futebol",
        "principalmente", "recentemente",
        # a torcao e' o evento, nao o sinal ("torci" nao e' "torto")
        "torceu", "torci", "torcendo", "torcao",
        # partes do corpo
        "braco", "bracos", "perna", "pernas", "cabeca", "pescoco", "costas", "coluna", "peito", "barriga",
        "joelho", "tornozelo", "ombro", "quadril", "olho", "olhos", "boca", "rosto", "dedo", "dedos",
        "pele", "mao", "maos", "calcanhar", "batata",
    )
)  # fmt: skip


def _raizes(texto: str, prefixo: int = _PREFIXO) -> set[str]:
    return {p[:prefixo] for p in _bs.normalizar(texto).split() if len(p) >= prefixo}


def _sinais(texto: str) -> set[str]:
    return _raizes(texto) - _NAO_E_SINAL


def sinal_dito(falas: Iterable[str], pergunta: str) -> bool:
    """Alguma palavra de SINAL da pergunta aparece no que a pessoa escreveu?"""
    sinais = _sinais(pergunta)
    return any(sinais & _raizes(f) for f in falas)


def so_sim_com_sinal_dito(
    pre: Sequence[str], protocolo: _p.Protocolo, falas: Sequence[str]
) -> tuple[str, ...]:
    """A pre-leitura com cada "sim" sem a palavra do sinal no texto da pessoa trocado por "nao_dito"."""
    perguntas = protocolo.perguntas_de_alerta
    return tuple(
        "nao_dito" if r == "sim" and not sinal_dito(falas, perguntas[i].pergunta) else r
        for i, r in enumerate(pre)
    )


def _palavras_do_mapa(catalogo: _p.Catalogo) -> frozenset[str]:
    textos: list[str] = []
    for entrada in catalogo.mapa.values():
        textos.append(entrada.queixa)
        textos.extend(entrada.como_a_pessoa_fala)
    return frozenset(r for t in textos for r in _raizes(t, _PREFIXO_DA_QUEIXA)) - _GENERICAS_DO_MAPA


#: A queixa compara 5 letras ("contei" nao e' "contracao"; "torcendo" e "tornozelo" seguem distintos).
_PREFIXO_DA_QUEIXA: Final[int] = 5
#: Palavras dos exemplos do mapa que nao dizem queixa nenhuma sozinhas.
_GENERICAS_DO_MAPA: Final[frozenset[str]] = frozenset(
    p[:_PREFIXO_DA_QUEIXA]
    for p in (
        "medico", "medica", "consulta", "especialista", "profissional", "procurar", "preciso", "quero",
        "tenho", "estou", "esta", "muito", "muita", "sinto", "sentindo", "depois", "agora", "sempre", "ficou",
        "nada", "pergunta", "perguntas", "acho", "certo", "correto", "seria", "melhor", "nenhuma", "nenhum",
        "coisa", "coisas", "dependente", "dependentes", "queria", "falar", "saber", "ajuda", "ajudar",
    )
)  # fmt: skip


def ultima_queixa(falas_da_pessoa: Sequence[str], catalogo: _p.Catalogo) -> str:
    """A fala mais recente da pessoa que descreve um problema (uma palavra de queixa dos exemplos do mapa),
    ou "". `falas_da_pessoa` sem a mensagem atual, da mais antiga para a mais recente."""
    palavras = _palavras_do_mapa(catalogo)
    for fala in reversed(falas_da_pessoa):
        if _raizes(fala, _PREFIXO_DA_QUEIXA) & palavras:
            return fala
    return ""


__all__ = ["so_sim_com_sinal_dito", "sinal_dito", "ultima_queixa"]
