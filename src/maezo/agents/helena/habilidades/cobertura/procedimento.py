"""RESOLUCAO DE PROCEDIMENTO da habilidade `cobertura` (HEL-2; DL-0167, DL-0128): o texto da pessoa -> o item
do catalogo de procedimentos (rota `/procedimentos` do lago) de que ela fala, ou UMA pergunta, ou nada.

Modulo PURO: sem rede, sem modelo, sem log do texto da pessoa. E' seguranca: a Helena nunca pode responder
sobre o procedimento errado, entao nao ha' escolha por aproximacao (distancia de edicao, similaridade,
embedding, "o mais parecido") nem desempate por ordem, frequencia ou tamanho de texto. Na duvida:
`esclarecer`; sem casamento exato: `sem_candidato` (a habilidade transfere).

A REGRA EXATA DE CASAMENTO
==========================

Normalizacao (texto da pessoa, catalogo e sinonimos): minusculas, sem acento (NFKD), todo caractere fora de
`[a-z0-9]` vira espaco, tokens separados por espaco; as palavras de ligacao de `_PALAVRAS_DE_LIGACAO` ("de",
"do", "a", "com"...) saem. Um QUALIFICADOR e' uma palavra de `_QUALIFICADORES` (parte do corpo: "joelho",
"cranio"/"cabeca", "torax"/"peito"...), agrupada com as formas equivalentes.

1. CODIGO: um token de exatamente 8 digitos e' um codigo TUSS dito pela pessoa. Havendo codigo no texto, SO' o
   codigo vale (as palavras nao entram): casa o item cujo `codigo` e' igual, byte a byte. Codigo fora do
   catalogo = nenhum candidato.
2. SINONIMO (forma popular -> conceito oficial): cada entrada do YAML (`populares` -> `oficial`) e cada
   entrada do K2 (`sinonimos_extra`: popular -> oficiais) forma um CONCEITO, identificado pelo nome oficial
   normalizado sem a sigla final (entradas com o mesmo nome se juntam; o proprio nome oficial conta como forma
   popular). A forma popular casa quando a sequencia INTEIRA dos seus tokens aparece contigua nos tokens do
   texto. O conceito alcanca o item do catalogo cujos tokens (`descricao` + `tuss` + `grupo`) contem TODOS os
   tokens do nome oficial, ou contem a SIGLA final do oficial (a palavra toda em maiusculas no YAML: "US",
   "RX", "TC", "RM").
3. DESCRICAO DIRETA: o item casa quando TODOS os tokens significativos (fora ligacao e qualificador) da sua
   `descricao`, ou do seu `tuss`, estao no texto. Descricao sem token significativo (ex.: so' "Joelho") nunca
   casa direto.
4. DUPLO SENTIDO: casamento cujos tokens sao todos de `_EXIGEM_CONTEXTO` (palavra comum: "tomo", "mapa",
   "radio"...; ou nome de doenca/orgao: "catarata", "hernia", "glicose"...) so' vale se o texto tiver tambem
   uma palavra de `_MARCADORES` ("exame", "cirurgia", "fazer"...). Assim "tomo remedio" nao vira tomografia e
   "tenho catarata" nao vira cirurgia (regra 5 da HEL-2: doenca e sintoma nao casam por acidente).
5. QUALIFICADORES RESTRINGEM: cada grupo de qualificador presente no texto precisa estar no item (nos seus
   tokens, ou nas formas populares/nome do conceito que o alcancou). Item que nao tem o qualificador sai.
   "ressonancia do joelho" com so' ressonancia de cranio no catalogo = nenhum candidato, nunca o cranio.

Desfecho: 1 item -> `unico`; 2+ -> `esclarecer` com UMA pergunta (ate' 3 opcoes pela `descricao`, na ordem do
catalogo, que e' so' apresentacao; mais de 3 -> pergunta qual parte do corpo / qual exame, sem listar); 0 ->
`sem_candidato`.

ESCLARECIMENTO: se a mensagem atual nao tem termo de procedimento (nem codigo, nem sinonimo, nem descricao)
mas tem qualificador ("a do joelho"), ela se junta a ULTIMA fala anterior que tinha termo: os termos vem
dessa fala, os qualificadores das duas. Sem qualificador na mensagem atual, nao ha' juncao (pergunta sem
termo de procedimento -> `sem_candidato`). Resposta por posicao ("a primeira") nao e' entendida: transfere.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

import yaml

from maezo.agents import resolve_spec_dir
from maezo.ports.tina import ProcedimentoCatalogo

DesfechoDaResolucao = Literal["unico", "esclarecer", "sem_candidato"]

#: A versao do formato de `spec/conhecimento/cobertura/sinonimos.yaml` que este leitor aceita.
VERSAO_DOS_SINONIMOS: Final[int] = 1
#: No maximo quantas opcoes a pergunta de esclarecimento lista pelo nome.
MAX_OPCOES_LISTADAS: Final[int] = 3

PERGUNTA_PARTE_DO_CORPO: Final[str] = "Para eu te responder certo: o procedimento é de qual parte do corpo?"
PERGUNTA_QUAL_EXAME: Final[str] = (
    "Para eu te responder certo: qual é exatamente o exame ou procedimento? Se tiver o pedido médico, "
    "pode me dizer o nome que está escrito nele."
)

_VAZIO: Final[Mapping[str, Sequence[str]]] = MappingProxyType({})


#: Palavras de ligacao: nao contam como token (nem na frase popular, nem na descricao, nem no texto).
def _palavras(texto: str) -> frozenset[str]:
    return frozenset(texto.split())


_PALAVRAS_DE_LIGACAO: Final[frozenset[str]] = _palavras(
    "a o as os e de do da dos das em no na nos nas um uma uns umas com para pra pro por ou ao aos"
)
#: Qualificadores (parte do corpo), em grupos de formas equivalentes. Vocabulario, nao regra de negocio.
_QUALIFICADORES: Final[tuple[frozenset[str], ...]] = tuple(
    _palavras(grupo)
    for grupo in (
        "joelho joelhos",
        "ombro ombros",
        "cotovelo cotovelos",
        "punho punhos",
        "mao maos",
        "quadril",
        "tornozelo tornozelos",
        "pe pes",
        "cranio cabeca cerebro cranial craniana",
        "coluna",
        "lombar",
        "cervical",
        "torax peito toracico",
        "abdome abdomen abdominal barriga",
        "pelve pelvica pelvico",
        "mama mamas mamaria",
        "pescoco",
        "coracao cardiaco cardiaca",
        "face",
        "rim rins renal",
        "prostata",
        "utero",
    )
)
_TODOS_OS_QUALIFICADORES: Final[frozenset[str]] = frozenset().union(*_QUALIFICADORES)
#: Tokens de duplo sentido (palavra comum ou nome de doenca/orgao): sozinhos, exigem um marcador.
_EXIGEM_CONTEXTO: Final[frozenset[str]] = _palavras(
    "tomo mapa radio chapa esteira "
    "apendicite hernia disco catarata varizes fimose amigdalas adenoide pedra vesicula rim calculo renal "
    "tireoide glicose glicemia acucar sangue colesterol hemoglobina glicada"
)
#: Marcadores de procedimento: com um deles no texto, o termo de duplo sentido vale. SO' palavras que nomeiam
#: o procedimento: "autorizacao", "guia", "pedido", "fazer" e "tratamento" estao em quase toda pergunta de
#: cobertura e nao marcam nada ("tomo remedio, preciso de autorizacao?"; revisao independente, HEL-2).
_MARCADORES: Final[frozenset[str]] = _palavras("exame exames cirurgia operar operacao sessao sessoes")
_CODIGO_TUSS: Final[re.Pattern[str]] = re.compile(r"^\d{8}$")
_SIGLA: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2,4}$")
_FORA_DO_ALFABETO: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")
#: O que a pergunta de esclarecimento nunca pode ter (link ou promessa de cobertura).
_PROIBIDO_NA_PERGUNTA: Final[re.Pattern[str]] = re.compile(
    r"https?:|www\.|\.com|\bcobre\b|\bcobert|\bcoberto|\bconsta\b|\brol\b", re.IGNORECASE
)


class SinonimosInvalidosError(ValueError):
    """O arquivo de sinonimos nao existe, nao e' YAML ou nao passou na validacao do formato."""


@dataclass(frozen=True, slots=True)
class EntradaDeSinonimo:
    populares: tuple[str, ...]
    oficial: str
    fonte: str


@dataclass(frozen=True, slots=True)
class Sinonimos:
    versao: int
    entradas: tuple[EntradaDeSinonimo, ...]


@dataclass(frozen=True, slots=True)
class Resolucao:
    """A SAIDA. `pergunta` so' em `esclarecer`; `candidatos` na ordem do catalogo (apresentacao, nao
    escolha)."""

    desfecho: DesfechoDaResolucao
    candidatos: tuple[ProcedimentoCatalogo, ...] = ()
    pergunta: str | None = None


# --- Carga dos sinonimos (falha fechada) -----------------------------------------------------------------
def caminho_padrao() -> Path:
    return resolve_spec_dir() / "conhecimento" / "cobertura" / "sinonimos.yaml"


def carregar_sinonimos(caminho: Path | None = None) -> Sinonimos:
    """Le' e valida o YAML de sinonimos. Qualquer desvio do formato levanta `SinonimosInvalidosError`."""
    alvo = caminho or caminho_padrao()
    try:
        bruto = yaml.safe_load(alvo.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SinonimosInvalidosError(f"sinonimos ilegiveis: {type(exc).__name__}") from exc
    return validar_sinonimos(bruto)


def _texto_nao_vazio(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def validar_sinonimos(bruto: object) -> Sinonimos:
    if not isinstance(bruto, Mapping):
        raise SinonimosInvalidosError("os sinonimos tem de ser um mapa YAML")
    if set(bruto) != {"versao", "sinonimos"}:
        raise SinonimosInvalidosError("chaves do topo tem de ser exatamente `versao` e `sinonimos`")
    versao = bruto["versao"]
    if isinstance(versao, bool) or versao != VERSAO_DOS_SINONIMOS:
        raise SinonimosInvalidosError(f"versao nao suportada (esperada {VERSAO_DOS_SINONIMOS})")
    lista = bruto["sinonimos"]
    if not isinstance(lista, list) or not lista:
        raise SinonimosInvalidosError("`sinonimos` tem de ser uma lista nao vazia")
    entradas: list[EntradaDeSinonimo] = []
    for posicao, item in enumerate(lista):
        if not isinstance(item, Mapping) or set(item) != {"populares", "oficial", "fonte"}:
            raise SinonimosInvalidosError(f"entrada {posicao}: campos tem de ser populares, oficial e fonte")
        populares = item["populares"]
        if (
            not isinstance(populares, list)
            or not populares
            or not all(_texto_nao_vazio(p) and _tokens(p) for p in populares)
        ):
            raise SinonimosInvalidosError(f"entrada {posicao}: `populares` invalido")
        if not _texto_nao_vazio(item["oficial"]) or not _tokens(item["oficial"]):
            raise SinonimosInvalidosError(f"entrada {posicao}: `oficial` invalido")
        if not _texto_nao_vazio(item["fonte"]):
            raise SinonimosInvalidosError(f"entrada {posicao}: `fonte` obrigatoria")
        entradas.append(
            EntradaDeSinonimo(
                populares=tuple(str(p) for p in populares),
                oficial=str(item["oficial"]),
                fonte=str(item["fonte"]),
            )
        )
    return Sinonimos(versao=VERSAO_DOS_SINONIMOS, entradas=tuple(entradas))


# --- Normalizacao --------------------------------------------------------------------------------------
def _tokens(texto: object) -> tuple[str, ...]:
    """Minusculas, sem acento, sem pontuacao, sem palavra de ligacao."""
    if not isinstance(texto, str):
        return ()
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c)
    )
    return tuple(t for t in _FORA_DO_ALFABETO.sub(" ", sem_acento).split() if t not in _PALAVRAS_DE_LIGACAO)


def _contem_frase(tokens: tuple[str, ...], frase: tuple[str, ...]) -> bool:
    n = len(frase)
    return n > 0 and any(tokens[i : i + n] == frase for i in range(len(tokens) - n + 1))


def _grupos_de_qualificador(tokens: frozenset[str]) -> tuple[frozenset[str], ...]:
    return tuple(g for g in _QUALIFICADORES if g & tokens)


# --- O indice (catalogo + conceitos) -------------------------------------------------------------------
@dataclass(slots=True)
class _Conceito:
    nome: tuple[str, ...]
    sigla: str | None = None
    frases: set[tuple[str, ...]] = field(default_factory=set)

    def vocabulario(self) -> frozenset[str]:
        return frozenset(self.nome).union(*self.frases)


@dataclass(frozen=True, slots=True)
class _Analise:
    """O que a resolucao enxerga de UMA mensagem (nunca o texto em si fora daqui)."""

    #: Por item do catalogo (indice), o vocabulario do conceito que o alcancou (vazio = descricao/codigo).
    casamentos: Mapping[int, frozenset[str]]
    tem_termo: bool
    qualificadores: frozenset[str]


def _conceito(conceitos: dict[tuple[str, ...], _Conceito], oficial: str) -> _Conceito | None:
    brutos = oficial.split()
    sigla = brutos[-1].lower() if len(brutos) > 1 and _SIGLA.match(brutos[-1]) else None
    nome = _tokens(" ".join(brutos[:-1]) if sigla else oficial)
    if not nome:
        return None
    atual = conceitos.setdefault(nome, _Conceito(nome=nome))
    if sigla and atual.sigla is None:
        atual.sigla = sigla
    atual.frases.add(nome)
    return atual


def _conceitos(sinonimos: Sinonimos, sinonimos_extra: Mapping[str, Sequence[str]]) -> tuple[_Conceito, ...]:
    conceitos: dict[tuple[str, ...], _Conceito] = {}
    for entrada in sinonimos.entradas:
        alvo = _conceito(conceitos, entrada.oficial)
        if alvo is not None:
            alvo.frases.update(f for f in (_tokens(p) for p in entrada.populares) if f)
    for popular, oficiais in sinonimos_extra.items():
        frase = _tokens(popular)
        if not frase or isinstance(oficiais, str) or not isinstance(oficiais, Sequence):
            continue  # entrada do K2 fora do formato: ignorada (so' reduz casamentos)
        for oficial in oficiais:
            alvo = _conceito(conceitos, oficial) if isinstance(oficial, str) else None
            if alvo is not None:
                alvo.frases.add(frase)
    return tuple(conceitos.values())


@dataclass(frozen=True, slots=True)
class _Indice:
    catalogo: tuple[ProcedimentoCatalogo, ...]
    tokens_do_item: tuple[frozenset[str], ...]
    conceitos: tuple[_Conceito, ...]

    @classmethod
    def montar(
        cls,
        catalogo: Sequence[ProcedimentoCatalogo],
        sinonimos: Sinonimos,
        sinonimos_extra: Mapping[str, Sequence[str]],
    ) -> _Indice:
        itens = tuple(catalogo)
        tokens = tuple(
            frozenset(_tokens(i.descricao)) | frozenset(_tokens(i.tuss)) | frozenset(_tokens(i.grupo))
            for i in itens
        )
        return cls(catalogo=itens, tokens_do_item=tokens, conceitos=_conceitos(sinonimos, sinonimos_extra))

    def _alcanca(self, conceito: _Conceito, indice: int) -> bool:
        tokens = self.tokens_do_item[indice]
        return set(conceito.nome) <= tokens or (conceito.sigla is not None and conceito.sigla in tokens)

    def analisar(self, texto: str) -> _Analise:
        tokens = _tokens(texto)
        conjunto = frozenset(tokens)
        qualificadores = conjunto & _TODOS_OS_QUALIFICADORES
        codigos = {t for t in tokens if _CODIGO_TUSS.match(t)}
        casamentos: dict[int, frozenset[str]] = {}
        if codigos:
            for i, item in enumerate(self.catalogo):
                if item.codigo.strip() in codigos:
                    casamentos[i] = frozenset()
            return _Analise(casamentos=casamentos, tem_termo=True, qualificadores=qualificadores)
        com_contexto = bool(conjunto & _MARCADORES)

        def vale(casados: set[str] | frozenset[str]) -> bool:
            return com_contexto or not casados <= _EXIGEM_CONTEXTO

        tem_termo = False
        for conceito in self.conceitos:
            frases = [f for f in conceito.frases if _contem_frase(tokens, f) and vale(set(f))]
            if not frases:
                continue
            tem_termo = True
            vocabulario = conceito.vocabulario()
            for i in range(len(self.catalogo)):
                if self._alcanca(conceito, i):
                    casamentos[i] = casamentos.get(i, frozenset()) | vocabulario
        for i, item in enumerate(self.catalogo):
            for fonte in (item.descricao, item.tuss):
                significativos = set(_tokens(fonte)) - _TODOS_OS_QUALIFICADORES
                if significativos and significativos <= conjunto and vale(significativos):
                    tem_termo = True
                    casamentos.setdefault(i, frozenset())
        return _Analise(casamentos=casamentos, tem_termo=tem_termo, qualificadores=qualificadores)

    def filtrar(self, casamentos: Mapping[int, frozenset[str]], qualificadores: frozenset[str]) -> list[int]:
        grupos = _grupos_de_qualificador(qualificadores)
        return [
            i for i in sorted(casamentos) if all(g & (self.tokens_do_item[i] | casamentos[i]) for g in grupos)
        ]


# --- A pergunta de esclarecimento ----------------------------------------------------------------------
def _pergunta(candidatos: Sequence[ProcedimentoCatalogo], tokens: Sequence[frozenset[str]]) -> str:
    descricoes = [c.descricao.strip() for c in candidatos]
    distintas = len({_tokens(d) for d in descricoes}) == len(descricoes) and all(descricoes)
    if len(candidatos) <= MAX_OPCOES_LISTADAS and distintas:
        opcoes = f"{', '.join(descricoes[:-1])} ou {descricoes[-1]}"
        texto = f"Para eu te responder certo, qual destes é o seu: {opcoes}?"
        if not _PROIBIDO_NA_PERGUNTA.search(texto):
            return texto
    if all(t & _TODOS_OS_QUALIFICADORES for t in tokens):
        return PERGUNTA_PARTE_DO_CORPO
    return PERGUNTA_QUAL_EXAME


def _decidir(indice: _Indice, posicoes: Sequence[int]) -> Resolucao:
    vistos: set[str] = set()
    escolhidas: list[int] = []
    for i in posicoes:
        codigo = indice.catalogo[i].codigo
        if codigo not in vistos:
            vistos.add(codigo)
            escolhidas.append(i)
    candidatos = tuple(indice.catalogo[i] for i in escolhidas)
    if not candidatos:
        return Resolucao(desfecho="sem_candidato")
    if len(candidatos) == 1:
        return Resolucao(desfecho="unico", candidatos=candidatos)
    pergunta = _pergunta(candidatos, [indice.tokens_do_item[i] for i in escolhidas])
    return Resolucao(desfecho="esclarecer", candidatos=candidatos, pergunta=pergunta)


def resolver(
    texto: str,
    *,
    catalogo: Sequence[ProcedimentoCatalogo],
    sinonimos: Sinonimos,
    falas_anteriores: Sequence[str] = (),
    sinonimos_extra: Mapping[str, Sequence[str]] = _VAZIO,
) -> Resolucao:
    """O texto da pessoa (e, para o esclarecimento, as falas anteriores) -> `Resolucao`. Regra no docstring
    do modulo. Nada aqui vai a log."""
    indice = _Indice.montar(catalogo, sinonimos, sinonimos_extra)
    atual = indice.analisar(texto)
    if atual.tem_termo:
        return _decidir(indice, indice.filtrar(atual.casamentos, atual.qualificadores))
    if atual.qualificadores:
        for anterior in reversed(tuple(falas_anteriores)):
            antes = indice.analisar(anterior)
            if antes.tem_termo:
                qualificadores = antes.qualificadores | atual.qualificadores
                return _decidir(indice, indice.filtrar(antes.casamentos, qualificadores))
    return Resolucao(desfecho="sem_candidato")


__all__ = [
    "MAX_OPCOES_LISTADAS",
    "PERGUNTA_PARTE_DO_CORPO",
    "PERGUNTA_QUAL_EXAME",
    "VERSAO_DOS_SINONIMOS",
    "DesfechoDaResolucao",
    "EntradaDeSinonimo",
    "Resolucao",
    "Sinonimos",
    "SinonimosInvalidosError",
    "caminho_padrao",
    "carregar_sinonimos",
    "resolver",
    "validar_sinonimos",
]
