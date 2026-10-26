"""A escolha do protocolo SEM o modelo, pelo jeito de falar (teste do dono de 26/10/2026).

Logs `/ecs/maezo-operadora-dev/agent-helena` (26/10, 16:41 e 16:58 UTC): a chamada da escolha caiu em
`ReadTimeout` (`helena_protocolo_modelo_falhou etapa=escolha`) e toda queixa virou o alerta universal
(`helena_protocolo_escolhido protocolo=uni-alerta-universal`) — "entorse no tornozelo" e "palpitacoes e
cansaco" receberam as perguntas genericas no lugar das da queixa. Sem o modelo, a queixa ainda e' lida pelo
codigo, antes do alerta universal:

1. FORTE: o titulo do protocolo ("Palpitacoes") ou um sinonimo da base de saude (`spec/conhecimento/saude/
   sintoma/sin-*.yaml`, "dor nas articulacoes", "cansaco") ligado ao protocolo por `_SINTOMA_DO_PROTOCOLO`. A
   queixa dita PRIMEIRO na mensagem vence ("palpitacoes e cansaco" -> palpitacoes);
2. FRACO (sem nada forte): as palavras de conteudo dos exemplos `como_a_pessoa_fala` ("tornozelo"); vence o
   protocolo unico com mais palavras.

Nada casou (ou empate) = `None`: o alerta universal, como antes. Crianca nao entra aqui: a faixa depende da
idade e o alerta universal da crianca vale para qualquer idade.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from maezo.conhecimento import base_saude as _bs
from maezo.conhecimento import protocolos as _p

#: O sintoma da base de saude -> o protocolo do publico (os ids ausentes do catalogo sao ignorados).
_SINTOMA_DO_PROTOCOLO: Final[Mapping[str, Mapping[str, str]]] = {
    "adulto": {
        "sin-batida-na-cabeca": "tri-adulto-queda-ou-trauma",
        "sin-queda": "tri-adulto-queda-ou-trauma",
        "sin-cansaco": "tri-adulto-mal-estar",
        "sin-dor-ao-urinar": "tri-adulto-dor-ao-urinar",
        "sin-dor-de-barriga": "tri-adulto-dor-de-barriga",
        "sin-dor-de-cabeca": "tri-adulto-dor-de-cabeca",
        "sin-dor-de-garganta": "tri-adulto-dor-de-garganta",
        "sin-dor-nas-articulacoes": "tri-adulto-dor-no-braco-ou-perna",
        "sin-dor-nas-costas": "tri-adulto-dor-nas-costas",
        "sin-dor-no-peito": "tri-adulto-dor-no-peito",
        "sin-falta-de-ar": "tri-adulto-falta-de-ar",
        "sin-febre-adulto": "tri-adulto-sinusite-gripe",
        "sin-tosse": "tri-adulto-sinusite-gripe",
        "sin-manchas-na-pele": "tri-adulto-manchas-na-pele",
        "sin-tontura": "tri-adulto-tontura",
        "sin-vomito-diarreia": "tri-adulto-diarreia-e-vomito",
    },
    "gestante": {
        "sin-febre-adulto": "gest-febre-infeccao",
        "sin-dor-ao-urinar": "gest-queixa-urinaria",
        "sin-falta-de-ar": "gest-falta-de-ar",
        "sin-vomito-diarreia": "gest-vomitos",
        "sin-dor-de-barriga": "gest-dor-barriga-contracoes",
        "sin-dor-de-cabeca": "gest-dor-de-cabeca-visao-inchaco",
    },
}
#: Palavras sem conteudo de QUEIXA (o "dor" esta' em quase todo protocolo; os verbos e tempos nao dizem nada).
_SEM_CONTEUDO: Final[frozenset[str]] = frozenset(
    {
        "dor", "dore", "doendo", "doi", "doe", "estou", "esta", "estar", "minha", "meu", "muito", "muita",
        "com", "sem", "para", "pra", "que", "nao", "uma", "um", "depois", "desde", "ontem", "hoje", "dias",
        "semana", "forte", "fort", "mais", "menos", "todo", "toda", "tudo", "sinto", "sentindo", "tenho",
        "acho", "quando", "isso", "essa", "esse", "voce", "ficou", "fico", "faz", "fazer", "deu", "tive",
        "mal", "bem", "coisa", "corpo", "lado", "vez", "vezes", "hora", "horas", "agora", "ainda", "pouco",
        "muitas", "tambem", "porque", "mae", "pai", "marido", "esposa", "filho", "filha", "pessoa", "ele",
        "ela", "nas", "nos", "das", "dos", "pelo", "pela", "sobre", "alguma", "algum", "saude", "problema",
        "sintoma", "estranho", "estranha", "sei", "explicar", "passando", "ruim", "repente", "errado",
        # Ambiguas sozinhas ("pressao estranha" pode ser no peito; "crise" de ansiedade ou de pressao) e a
        # pergunta pelo profissional: sem o modelo, nao escolhem protocolo (o alerta universal pergunta tudo).
        "pressa", "crise", "profis", "procur", "devo", "medico", "consul", "tipo", "qual",
    }
)  # fmt: skip
_MINIMO: Final[int] = 4


def _raiz(palavra: str) -> str:
    """Raiz curta para plural/genero ("dores"->"dor", "articulacoes"/"articulacao"->"articu")."""
    if len(palavra) > 4 and palavra.endswith("es"):
        palavra = palavra[:-2]
    elif len(palavra) > 3 and palavra.endswith("s"):
        palavra = palavra[:-1]
    return palavra[:6]


#: As palavras sem conteudo na MESMA forma das raizes comparadas ("estranha" -> "estran", "sentindo" ->
#: "sentin"): sem isso, as palavras longas da lista nunca casavam.
_SEM_CONTEUDO_RAIZ: Final[frozenset[str]] = frozenset({*_SEM_CONTEUDO, *(_raiz(p) for p in _SEM_CONTEUDO)})


def _raizes(texto: str) -> list[str]:
    return [_raiz(p) for p in _bs.normalizar(texto).split()]


def _conteudo(texto: str) -> set[str]:
    return {r for r in _raizes(texto) if len(r) >= _MINIMO and r not in _SEM_CONTEUDO_RAIZ}


def _posicao(raizes: list[str], termo: list[str]) -> int | None:
    """Onde TODAS as raizes do termo aparecem na mensagem (a primeira delas), ou `None`."""
    posicoes = []
    for r in termo:
        if r not in raizes:
            return None
        posicoes.append(raizes.index(r))
    return min(posicoes) if posicoes else None


def escolher(catalogo: _p.Catalogo, base: _bs.BaseSaude, texto: str, publico: str) -> str | None:
    """O id do protocolo da queixa da mensagem pelo jeito de falar, ou `None` (o alerta universal)."""
    if publico not in _SINTOMA_DO_PROTOCOLO and publico != "puerpera":
        return None
    grupo = "gestante" if publico == "puerpera" else publico
    candidatos = {
        pid: p
        for pid, p in catalogo.protocolos.items()
        if p.publico == publico and not pid.startswith("uni-")
    }
    if not candidatos:
        return None
    raizes = _raizes(texto)
    fortes: dict[str, int] = {}

    def _forte(pid: str, pos: int | None) -> None:
        if pid in candidatos and pos is not None:
            fortes[pid] = min(fortes.get(pid, pos), pos)

    for pid, protocolo in candidatos.items():
        for r in _conteudo(protocolo.titulo):
            _forte(pid, _posicao(raizes, [r]))
    for sin_id, pid in _SINTOMA_DO_PROTOCOLO.get(grupo, {}).items():
        assunto = base.get(sin_id)
        if assunto is None:
            continue
        for sinonimo in (*assunto.sinonimos, assunto.titulo):
            termo = [r for r in _raizes(sinonimo) if len(r) >= 3 and r not in _SEM_CONTEUDO_RAIZ]
            _forte(pid, _posicao(raizes, termo) if termo else None)
    if fortes:
        primeiro = min(fortes.values())
        ganhadores = sorted(pid for pid, pos in fortes.items() if pos == primeiro)
        return ganhadores[0] if len(ganhadores) == 1 else None
    mensagem = _conteudo(texto)
    fracos = {
        pid: len(mensagem & set().union(*(_conteudo(e) for e in p.como_a_pessoa_fala)))
        for pid, p in candidatos.items()
    }
    maior = max(fracos.values(), default=0)
    ganhadores = [pid for pid, n in fracos.items() if n == maior]
    return ganhadores[0] if maior > 0 and len(ganhadores) == 1 else None


__all__ = ["escolher"]
