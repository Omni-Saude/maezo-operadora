"""QUAL ESPECIALISTA procurar (teste do dono de 26/10/2026). Modulo PURO (sem grafo, sem rede).

No teste do dono, "que tipo de profissional eu devo procurar?" recebeu "Eu nao avalio sintomas, entao nao
consigo dizer se voce precisa de medico..." (a navegacao de sempre) e "o correto nao seria um ortopedista?"
recebeu "Consulta com X e' consulta de especialista. Por aqui eu nao pesquiso especialistas..." (texto da v4).
Agora:
- a pergunta de QUAL profissional (sem pedido de marcar) recebe o especialista do MAPA do protocolo
  (`especialista/mapa_queixa_especialista.yaml` + `textos.yaml`) e os canais, sem prazo da ANS;
- com relato de queixa na mesma mensagem ("estou sentindo palpitacoes, que profissional procuro?"), as
  perguntas de alerta do protocolo vem antes (seguranca) e o especialista do mapa vai na resposta final;
- a SUGESTAO da pessoa ("nao seria melhor um reumatologista?") recebe o especialista do mapa para a queixa da
  conversa; a sugestao e' aceita so' quando esta' entre as alternativas do mapa (nunca inventar).
O pedido de MARCAR ("quero marcar", "consigo agendar por aqui?") continua com o agendamento de sempre.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar
from maezo.conhecimento import protocolos as _p

#: A pergunta de QUAL profissional/especialista procurar (texto normalizado, sem acento).
_QUAL_ESPECIALISTA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:que|qual|quais)(?: (?:tipo|tipos) de)? (?:profissional|profissionais|medic[oa]s?|especialistas?"
    r"|especialidades?|doutor[a]?)\b"
    r"|\bcom quem (?:eu )?(?:devo|posso|preciso|tenho que|vou)\b"
    r"|\bquem (?:eu )?(?:devo|posso|preciso|tenho que) (?:procurar|consultar|ver)\b"
    r"|\b(?:devo|preciso|posso|tenho que|quero|queria|vou) (?:procurar|buscar|consultar|ver)"
    r"(?: com)? (?:um |uma |o |a )?(?:profissional|medic[oa]|especialista|doutor[a]?)\b"
)
#: Pedido de MARCAR/AGENDAR: fica com o agendamento de sempre.
_MARCAR: Final[re.Pattern[str]] = re.compile(r"\b(?:marc|agend|reserv|remarc|desmarc)\w*")
#: Relato de queixa ATUAL na mensagem (de quem escreve ou de outra pessoa): "estou sentindo", "ela esta' com".
_RELATO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:estou|to|tou|ando|estamos|esta|ta|anda|tenho|tem) (?:sentindo|com|tendo|sofrendo|sentido|tido)\b"
    r"|\b(?:sinto|senti|sente)\b|\bme doi\b|\b(?:esta|ta) doendo\b"
)
#: A pessoa SUGERE/CONFERE um especialista ("o correto nao seria...", "nao seria melhor...", "voce nao acha").
_SUGESTAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:seria|nao acha|voce acha|vc acha|acha que|correto|certo|melhor|deveria|o ideal|nao e|nao era"
    r"|indicad[oa]|recomenda\w*)\b"
)
#: Os nomes leigos que NAO sao uma sugestao de especialista (areas de atuacao genericas).
_NAO_SUGESTAO: Final[frozenset[str]] = frozenset({"tratamento da dor", "medicina do sono"})

# --- Textos (constantes do modulo: o pacote nao tem texto equivalente) -------------------------------------
TEXTO_ACEITA: Final[str] = "Sim, {especialistas} {verbo} uma boa opção para {queixa}."
TEXTO_OUTRO: Final[str] = "Sobre {especialistas}: se for preciso, o médico indica na consulta."


def _sem_artigo(leigo: str) -> str:
    return re.sub(r"^(?:o|a|os|as) ", "", leigo.strip())


def _padrao_do_nome(nome: str) -> str:
    """ "ortopedista" -> ortopedistas?, "clinico geral" -> clinic[oa]s? geral, "cirurgiao" -> cirurgiao|a."""
    partes: list[str] = []
    for palavra in normalizar(nome).split():
        if palavra.endswith("ao"):
            partes.append(re.escape(palavra[:-2]) + r"(?:ao|a|oes)")
        elif palavra.endswith("o"):
            partes.append(re.escape(palavra[:-1]) + r"[oa]s?")
        else:
            partes.append(re.escape(palavra) + "s?")
    return r"\b" + r"\s+".join(partes) + r"\b"


@lru_cache(maxsize=8)
def _padroes(nomes: tuple[tuple[str, str], ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """(nome leigo com artigo, padrao) de cada nome leigo de `textos.yaml` (e o apelido entre parenteses)."""
    saida: list[tuple[str, re.Pattern[str]]] = []
    vistos: set[str] = set()
    for _, leigo in nomes:
        if leigo in vistos or leigo in _NAO_SUGESTAO:
            continue
        vistos.add(leigo)
        principal, _, apelido = _sem_artigo(leigo).partition(" (")
        alternativas = [principal, apelido.rstrip(")")] if apelido else [principal]
        padrao = "|".join(_padrao_do_nome(a) for a in alternativas if a)
        saida.append((leigo, re.compile(padrao)))
    return tuple(saida)


def _chave(textos: _p.TextosDoEspecialista) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(textos.nomes_leigos.items()))


def quer_marcar(texto: str) -> bool:
    return _MARCAR.search(normalizar(texto)) is not None


def pergunta_qual_especialista(texto: str) -> bool:
    """ "Que tipo de profissional eu devo procurar?", "com qual medico me consulto?", "quero procurar um
    medico para atende-la" — sem pedido de marcar."""
    plano = normalizar(texto)
    return _QUAL_ESPECIALISTA.search(plano) is not None and _MARCAR.search(plano) is None


def relata_queixa(texto: str) -> bool:
    """A mensagem RELATA uma queixa atual ("estou sentindo palpitacoes", "ela esta' com febre")? Pergunta
    informativa ("que medico trata enxaqueca?", "para um homem com PSA alterado") nao relata."""
    return _RELATO.search(normalizar(texto)) is not None


def sugeridos(texto: str, textos: _p.TextosDoEspecialista) -> list[str]:
    """Os especialistas (nome leigo com artigo, de `textos.yaml`) citados na mensagem, na ordem da fala."""
    plano = normalizar(texto)
    achados: list[tuple[int, str]] = []
    for leigo, padrao in _padroes(_chave(textos)):
        m = padrao.search(plano)
        if m is not None:
            achados.append((m.start(), leigo))
    return [leigo for _, leigo in sorted(achados)]


def sugere_especialista(texto: str, textos: _p.TextosDoEspecialista) -> bool:
    """ "O correto nao seria ortopedista?", "nao seria melhor um reumatologista?", "voce nao acha que seria
    ortopedista ou fisioterapeuta?": um especialista citado + forma de sugestao, sem pedido de marcar."""
    plano = normalizar(texto)
    if _MARCAR.search(plano) is not None or _SUGESTAO.search(plano) is None:
        return False
    return bool(sugeridos(texto, textos))


def alternativas_do_mapa(entrada: _p.EntradaDoMapa, textos: _p.TextosDoEspecialista) -> frozenset[str]:
    """Os nomes leigos que o mapa admite para a queixa: a porta de entrada e os especialistas de depois."""
    nomes = [*entrada.especialista_primeiro.cfm]
    nomes += [e.cfm or e.profissao or "" for e in entrada.especialista_depois]
    return frozenset(leigo for n in nomes if n and (leigo := textos.nome_leigo(n)) is not None)


def alternativas_do_protocolo(protocolo: _p.Protocolo, textos: _p.TextosDoEspecialista) -> frozenset[str]:
    """Sem o mapa (modelo fora): o especialista do protocolo e o clinico geral (a porta de entrada)."""
    nomes = re.split(r"\s+ou\s+|,|/", protocolo.especialista.depois)
    leigos = {leigo for n in nomes if (leigo := textos.nome_leigo(n.strip())) is not None}
    return frozenset({*leigos, "o clínico geral", "o médico de família"})


def _lista(nomes: Sequence[str]) -> str:
    return nomes[0] if len(nomes) == 1 else f"{', '.join(nomes[:-1])} e {nomes[-1]}"


def texto_da_sugestao(
    sugestoes: Sequence[str], alternativas: frozenset[str], queixa: str | None, especialista: str
) -> str:
    """A resposta a sugestao: aceita o que o mapa admite, devolve ao medico o que ele nao admite, e explica o
    especialista do mapa (`especialista`: a frase do mapa com o clinico e os canais)."""
    aceitos = [s for s in sugestoes if s in alternativas] if queixa else []
    outros = [s for s in sugestoes if s not in aceitos]
    partes: list[str] = []
    if aceitos:
        verbo = "é" if len(aceitos) == 1 else "são"
        partes.append(TEXTO_ACEITA.format(especialistas=_lista(aceitos), verbo=verbo, queixa=queixa))
    if outros:
        partes.append(TEXTO_OUTRO.format(especialistas=_lista(outros)))
    partes.append(especialista)
    return " ".join(partes)


__all__ = [
    "TEXTO_ACEITA",
    "TEXTO_OUTRO",
    "alternativas_do_mapa",
    "alternativas_do_protocolo",
    "pergunta_qual_especialista",
    "quer_marcar",
    "relata_queixa",
    "sugere_especialista",
    "sugeridos",
    "texto_da_sugestao",
]
