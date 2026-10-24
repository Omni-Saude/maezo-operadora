"""ASSUNTOS ABERTOS da conversa (Plano de ajustes da Helena v2, etapa 9; DL-0154).

A lista fica no CODIGO (estado da conversa), alimentada pelos `topic_events` do roteador v7 ("abre",
"fecha", "retoma") e pela habilidade do turno. Ela serve a UMA coisa: quando a conversa volta de outro
assunto — a resposta do Lucas, ou um "ok, obrigado" depois de um aviso —, a Helena pergunta UMA vez sobre o
que ficou para tras: "Sobre o assunto de saude, ficou alguma duvida?". Cada assunto e' retomado no maximo uma
vez; o modelo nunca escreve a lista.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from maezo.agents.helena import roteador_v7 as _rv7

CHAVE_RETOMADA: Final[str] = "retomada_de_assunto"
TEXTO_RETOMADA: Final[str] = "Sobre {assunto}, ficou alguma dúvida?"
#: So' estes assuntos sao retomados (os outros nao tem pergunta que faca sentido depois).
NOMES_DOS_ASSUNTOS: Final[Mapping[str, str]] = {
    "saude": "o assunto de saúde",
    "plano": "os dados do seu plano",
    "lucas_cobranca": "a mensalidade",
    "lucas_pedidos": "o seu pedido ao plano",
}
_MAXIMO: Final[int] = 6

PADRAO: Final[Mapping[str, str]] = {CHAVE_RETOMADA: TEXTO_RETOMADA}


def validos(valor: object) -> list[dict[str, Any]]:
    """A lista do estado, validada na fronteira (o que nao tem a forma certa nao atravessa)."""
    if not isinstance(valor, list):
        return []
    saida = [
        {"t": item["t"], "r": item["r"]}
        for item in valor
        if isinstance(item, Mapping) and item.get("t") in _rv7.TOPICOS and isinstance(item.get("r"), bool)
    ]
    return saida[-_MAXIMO:]


def depois_do_turno(antes: Sequence[Mapping[str, Any]], v7: _rv7.DecisaoV7) -> list[dict[str, Any]]:
    """A lista depois dos eventos do turno: "abre" (e a habilidade do turno) entra; "fecha" sai; "retoma"
    marca como retomado (a pessoa voltou a ele: nao ha' o que perguntar)."""
    lista = [dict(item) for item in antes]
    topicos = [item["t"] for item in lista]

    def abrir(topico: str) -> None:
        if topico in _rv7.TOPICOS and topico not in topicos:
            lista.append({"t": topico, "r": False})
            topicos.append(topico)

    if v7.skill in _rv7.TOPICOS:
        # O assunto do turno vai para o FIM (o mais recente): ele e' o que acabou de ser tratado.
        atual = next((item for item in lista if item["t"] == v7.skill), {"t": v7.skill, "r": False})
        lista = [item for item in lista if item["t"] != v7.skill] + [atual]
        topicos = [item["t"] for item in lista]
    for evento in v7.eventos:
        if evento.evento == "abre":
            abrir(evento.topico)
        elif evento.evento == "fecha":
            lista = [item for item in lista if item["t"] != evento.topico]
            topicos = [item["t"] for item in lista]
        elif evento.evento == "retoma":
            for item in lista:
                if item["t"] == evento.topico:
                    item["r"] = True
    return lista[-_MAXIMO:]


def a_retomar(lista: Sequence[Mapping[str, Any]], *, exceto: str | None) -> str | None:
    """O assunto mais antigo ainda nao retomado, entre os que tem pergunta — fora `exceto` (o do turno) e
    fora o MAIS RECENTE da lista (o que acabou de ser tratado: perguntar dele de novo seria repetir)."""
    recente = lista[-1]["t"] if lista else None
    for item in lista:
        if item["t"] not in (exceto, recente) and not item["r"] and item["t"] in NOMES_DOS_ASSUNTOS:
            return str(item["t"])
    return None


def marcar_retomado(lista: Sequence[Mapping[str, Any]], topico: str) -> list[dict[str, Any]]:
    return [{**item, "r": True} if item["t"] == topico else dict(item) for item in lista]


def texto_de_retomada(modelo: str, topico: str) -> str:
    return modelo.format(assunto=NOMES_DOS_ASSUNTOS[topico])


__all__ = [
    "CHAVE_RETOMADA",
    "NOMES_DOS_ASSUNTOS",
    "PADRAO",
    "TEXTO_RETOMADA",
    "a_retomar",
    "depois_do_turno",
    "marcar_retomado",
    "texto_de_retomada",
    "validos",
]
