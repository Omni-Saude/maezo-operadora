"""HISTORICO CURTO DA CONVERSA da Helena (DL-0080, atras de `MAEZO_HELENA_HISTORICO`, default off).

O DEFEITO MEDIDO EM 07/10/2026. A Helena nao mantinha conversa: `receive` zera toda saida a cada
turno (defesa T1.11), o classificador via so' a mensagem atual e a redacao nao recebia os turnos
anteriores. "Minha mae esta com febre moderada" seguido de "Ela tem 80 anos" chegava ao modelo
como duas mensagens sem relacao — a segunda sem sintoma nenhum, e a DMN nunca via o par
febre + 80 anos como um caso so'.

O QUE ESTE MODULO GUARDA, e so' isto: as ultimas `HISTORICO_MAX_TROCAS` mensagens da conversa,
cada uma `{papel, texto, em}` — `papel` no vocabulario fechado (`beneficiario`/`helena`), `texto`
cortado em `HISTORICO_TEXTO_MAX_CHARS`, `em` ISO-8601 UTC. NUNCA telefone, hash, pseudo-id,
identidade do beneficiario ou qualquer outro campo do estado: a entrada e' montada SO' a partir
de dois textos (a mensagem do turno e o texto que SAIU), e a validacao recusa qualquer chave a
mais. O texto da Helena gravado e' o ENVIADO — nunca o rascunho barrado por uma cerca de saida.

O QUE ELE NAO FAZ. Nao decide nada clinico: a regra continua 100% DMN/red flag. O historico so'
alimenta a EXTRACAO (o classificador ve o quadro acumulado) e a REDACAO do `inform` (responder
em continuidade). Ele viaja ao modelo SEMPRE como bloco NAO CONFIAVEL (`render_untrusted_block`).

VALIDADE. A mesma janela da memoria clinica: `HISTORICO_JANELA_HORAS` desde a ULTIMA mensagem
da pessoa (`ultima_mensagem_em` do turno anterior). Fora dela, ilegivel, no futuro ou malformado
-> `None`, que degrada exatamente para o comportamento de antes desta frente. Falha para `None`,
nunca para um historico parcial.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final

#: Quantas mensagens (entradas `{papel, texto, em}`) ficam. Cada entrada e' UMA troca de vez
#: (uma fala da pessoa ou da Helena). Seis cobrem as tres ultimas idas e voltas — o que a anafora
#: ("ela", "isso", "tambem") precisa — e cabem inteiras num bloco nao confiavel (6 x 500 < 4000).
HISTORICO_MAX_TROCAS: Final[int] = 6

#: Teto de cada texto guardado. O beneficiario raramente passa disso; a Helena tambem nao.
HISTORICO_TEXTO_MAX_CHARS: Final[int] = 500

#: A mesma janela da memoria clinica (`graph.MEMORIA_CLINICA_JANELA_HORAS`, igualdade fixada em
#: teste): a conversa de horas atras ainda e' a mesma conversa; a de ontem nao e'.
HISTORICO_JANELA_HORAS: Final[float] = 6.0

PAPEL_BENEFICIARIO: Final[str] = "beneficiario"
PAPEL_HELENA: Final[str] = "helena"
_PAPEIS: Final[frozenset[str]] = frozenset({PAPEL_BENEFICIARIO, PAPEL_HELENA})
_CHAVES: Final[frozenset[str]] = frozenset({"papel", "texto", "em"})


def _instante(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        quando = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return quando if quando.tzinfo is not None else quando.replace(tzinfo=UTC)


def historico_valido(
    bruto: Any,
    *,
    ultima_mensagem_em: Any,
    agora: datetime,
    janela_horas: float = HISTORICO_JANELA_HORAS,
) -> list[dict[str, str]] | None:
    """O historico que pode ser confiado, ou `None`.

    `ultima_mensagem_em` e' o carimbo do turno ANTERIOR (o que `receive` le' antes de gravar o do
    turno atual). Sem ele legivel, no futuro ou mais velho que a janela, o historico expira.
    Qualquer entrada malformada (chave a mais/a menos, papel fora do vocabulario, texto que nao e'
    string, carimbo ilegivel) derruba o historico inteiro — nunca um historico parcial.
    """
    if not isinstance(bruto, list) or not bruto:
        return None
    ultima = _instante(ultima_mensagem_em)
    if ultima is None:
        return None
    decorrido = (agora - ultima).total_seconds()
    if decorrido < 0 or decorrido > janela_horas * 3600:
        return None
    limpo: list[dict[str, str]] = []
    for entrada in bruto:
        if not isinstance(entrada, dict) or frozenset(entrada) != _CHAVES:
            return None
        papel, texto, em = entrada["papel"], entrada["texto"], entrada["em"]
        if not isinstance(papel, str) or papel not in _PAPEIS:
            return None
        if not isinstance(texto, str) or _instante(em) is None:
            return None
        limpo.append({"papel": papel, "texto": texto[:HISTORICO_TEXTO_MAX_CHARS], "em": str(em)})
    return limpo[-HISTORICO_MAX_TROCAS:]


def acrescentar_turno(
    historico: Sequence[dict[str, str]] | None,
    *,
    mensagem: str | None,
    resposta: str | None,
    agora: datetime,
) -> list[dict[str, str]]:
    """O historico com a fala da pessoa e a resposta ENVIADA deste turno no fim (vazias sao puladas).

    So' estes dois textos entram: a entrada e' construida aqui, chave por chave, entao nenhum outro
    campo do estado (telefone, hash, identidade) tem por onde chegar.
    """
    novo = [dict(item) for item in (historico or [])]
    carimbo = agora.isoformat()
    for papel, texto in ((PAPEL_BENEFICIARIO, mensagem), (PAPEL_HELENA, resposta)):
        if isinstance(texto, str) and texto.strip():
            novo.append({"papel": papel, "texto": texto.strip()[:HISTORICO_TEXTO_MAX_CHARS], "em": carimbo})
    return novo[-HISTORICO_MAX_TROCAS:]


def historico_em_texto(historico: Sequence[dict[str, str]]) -> str:
    """Uma linha por mensagem, em ordem cronologica: `beneficiario: ...` / `helena: ...`.

    E' o MIOLO do bloco nao confiavel `historico_conversa`; quem chama o embrulha.
    """
    return "\n".join(f"{item['papel']}: {item['texto']}" for item in historico)
