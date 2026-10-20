"""CATALOGO DE PROCEDIMENTOS e a TRADUCAO do nome popular para o nome OFICIAL (rodada do dono de 19/10/2026,
"FACA e RESOLVA"; DL-0128) — so' na allowlist da saude gerativa.

O QUE E'. A pessoa escreve "ultrassom da barriga", "exame do coracao com esteira", "cirurgia de vesicula"; o
cadastro de cobertura da AMH fala "ULTRASSONOGRAFIA ABDOMINAL TOTAL", "TESTE ERGOMETRICO",
"COLECISTECTOMIA". Este modulo encontra, no CATALOGO de procedimentos que a rota de cobertura resolve por
CODIGO (contrato AMH `tina-procedimentos`, manifest v1.14), os candidatos mais provaveis para o termo:

  * SIGNIFICADO: o mesmo modelo de embeddings REGIONAL da base de saude (Titan v2, sa-east-1, sem perfil
    global), indice EM MEMORIA do catalogo inteiro (matriz numpy), montado em SEGUNDO PLANO na subida e
    guardado enquanto a `versao_catalogo` for a mesma;
  * LEXICO: palavras do termo contra as palavras do nome (com tolerancia de prefixo: "pelve" ~ "pelvica");
  * SINONIMOS GENERICOS: a lista publica `spec/conhecimento/cobertura/sinonimos.yaml` (cada entrada com a
    fonte; escrita ANTES de qualquer resultado de teste) e os sinonimos ja' curados da base de saude
    (exames, procedimentos e cirurgias) expandem o termo antes das duas buscas.

O QUE NAO E'. Nunca diz se cobre: so' propoe ate' 3 candidatos com nota; quem diz e' a consulta de cobertura
pelo CODIGO. Nunca regra por termo especifico. Sem indice pronto (subida, falha do modelo), o lexico sozinho
responde; sem catalogo, `None` (o grafo cai na busca por texto da propria rota de cobertura).
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

import structlog
import yaml

from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

#: Nota combinada minima para um candidato ser PROPOSTO (abaixo: "nao localizei").
NOTA_MINIMA: Final[float] = 0.40
#: Nota e folga sobre o segundo para o primeiro ser escolhido SEM perguntar.
NOTA_CERTA: Final[float] = 0.60
FOLGA_CERTA: Final[float] = 0.07
#: Peso do significado na nota combinada (o resto e' o lexico).
PESO_SIGNIFICADO: Final[float] = 0.6
MAX_CANDIDATOS: Final[int] = 3
PRAZO_CONSULTA_S: Final[float] = 4.0
#: O catalogo inteiro (~10 mil itens) em lotes, com ritmo para nao estourar a cota por minuto do modelo.
LOTE_DO_INDICE: Final[int] = 200
PAUSA_ENTRE_LOTES_S: Final[float] = 2.5
_TEMPO_DE_ESPERA_APOS_FALHA_S: Final[float] = 300.0
_ESPERAS_DO_LOTE_S: Final[tuple[float, ...]] = (10.0, 30.0, 60.0, 0.0)
#: Candidatos que a RECUPERACAO entrega para a escolha do modelo (reordenacao).
MAX_RECUPERADOS: Final[int] = 20

_PALAVRAS_VAZIAS: Final[frozenset[str]] = frozenset(
    [
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "para",
        "pra",
        "pro",
        "por",
        "com",
        "sem",
        "que",
        "meu",
        "minha",
        "seu",
        "sua",
        "exame",
        "exames",
        "procedimento",
        "procedimentos",
        "cirurgia",
        "tratamento",
        "fazer",
        "plano",
        "cobre",
        "cobertura",
        "pedido",
        "medico",
        "solicitou",
        "pediu",
    ]
)


def normalizar(texto: str) -> str:
    """Minusculas, sem acento, so' letras, numeros e espaco."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento).split())


def palavras(texto: str) -> list[str]:
    return [p for p in normalizar(texto).split() if p not in _PALAVRAS_VAZIAS and len(p) >= 2]


@dataclass(frozen=True, slots=True)
class Procedimento:
    """Um item do catalogo (dado de REFERENCIA, sem pessoa)."""

    codigo: str
    descricao: str
    nome_tuss: str | None = None
    grupo: str | None = None

    @property
    def nome(self) -> str:
        """O nome legivel: o oficial TUSS quando houver, senao o do cadastro."""
        return " ".join((self.nome_tuss or self.descricao).split())

    def documento(self) -> str:
        partes = [self.nome]
        if self.nome_tuss and normalizar(self.nome_tuss) != normalizar(self.descricao):
            partes.append(self.descricao)
        return ". ".join(partes)


@dataclass(frozen=True, slots=True)
class Candidato:
    procedimento: Procedimento
    nota: float
    significado: float
    lexico: float


@dataclass(frozen=True, slots=True)
class Resultado:
    """`certo` = o primeiro pode ser consultado sem perguntar; `candidatos` (ate' 3) para as opcoes."""

    candidatos: tuple[Candidato, ...]
    certo: bool
    com_significado: bool

    @property
    def vazio(self) -> bool:
        return not self.candidatos


# --- Sinonimos genericos -----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Sinonimo:
    populares: tuple[str, ...]
    oficial: str
    fonte: str


def raiz_dos_sinonimos() -> Path:
    """`spec/conhecimento/cobertura/sinonimos.yaml` pelo mesmo ponto unico de `spec/` (`resolve_spec_dir`)."""
    from maezo.agents import resolve_spec_dir  # import tardio: evita ciclo no import do pacote

    return resolve_spec_dir() / "conhecimento" / "cobertura" / "sinonimos.yaml"


def carregar_sinonimos(caminho: Path | None = None) -> tuple[Sinonimo, ...]:
    """A lista publica. Entrada sem fonte, sem oficial ou sem popular reprova (falha FECHADA: lista vazia)."""
    alvo = caminho or raiz_dos_sinonimos()
    try:
        dados = yaml.safe_load(alvo.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.error("catalogo_sinonimos_indisponiveis")
        return ()
    saida: list[Sinonimo] = []
    for item in dados.get("sinonimos") or []:
        if not isinstance(item, Mapping):
            return ()
        populares = tuple(normalizar(str(p)) for p in item.get("populares") or [] if normalizar(str(p)))
        oficial = " ".join(str(item.get("oficial") or "").split())
        fonte = " ".join(str(item.get("fonte") or "").split())
        if not populares or not oficial or not fonte:
            return ()
        saida.append(Sinonimo(populares, oficial, fonte))
    return tuple(saida)


def sinonimos_da_base(base: Any) -> tuple[Sinonimo, ...]:
    """Os sinonimos JA' curados da base de saude (exames, procedimentos, cirurgias, materiais, vacinas)."""
    saida: list[Sinonimo] = []
    for assunto in getattr(base, "assuntos", {}).values():
        if getattr(assunto, "categoria", None) not in (
            "exame",
            "procedimento",
            "cirurgia",
            "material",
            "vacina",
        ):
            continue
        populares = tuple(normalizar(s) for s in getattr(assunto, "sinonimos", ()) if len(normalizar(s)) >= 3)
        if populares:
            saida.append(Sinonimo(populares, str(assunto.titulo), f"base de saude ({assunto.id})"))
    return tuple(saida)


def expandir(termo: str, sinonimos: Iterable[Sinonimo]) -> str:
    """O termo com o nome oficial de cada sinonimo popular que aparece nele (palavra inteira)."""
    plano = normalizar(termo)
    extras: list[str] = []
    for s in sinonimos:
        presente = any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", plano) for p in s.populares)
        if presente and normalizar(s.oficial) not in plano:
            extras.append(s.oficial)
    return f"{termo} ({'; '.join(extras)})" if extras else termo


# --- Lexico ------------------------------------------------------------------------------------------------
def _prefixo_comum(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _casa_palavra(palavra: str, outra: str) -> bool:
    """Mesma palavra, ou a mesma RAIZ (flexao: "pelve"/"pelvica", "cranio"/"craniana"): prefixo comum de 5+
    letras que deixa de fora no maximo as 2 ultimas letras da palavra mais curta."""
    if palavra == outra:
        return True
    curta = min(len(palavra), len(outra))
    return curta >= 5 and _prefixo_comum(palavra, outra) >= max(5, curta - 2)


def _casa(palavra: str, alvo: set[str]) -> bool:
    return palavra in alvo or any(_casa_palavra(palavra, a) for a in alvo)


#: Itens que ficam no catalogo mas perdem para o procedimento em si quando o significado e' parecido
#: (anestesia para exame, acompanhamento, junta, domiciliar).
_ADMINISTRATIVO: Final[re.Pattern[str]] = re.compile(
    r"^(?:taxa|aluguel|pacote|laudo|revisao|diaria|honorario|material|kit|remocao|transporte|junta|anestesia"
    r"|acompanhamento)\b|\bdomiciliar\b|\bpor procedimento\b"
)
PENALIDADE_ADMINISTRATIVO: Final[float] = 0.75

# --- UNIVERSO da busca: so' itens ASSISTENCIAIS (avaliacao independente de 20/10/2026) ---------------------
#: Regra de CATALOGO (nunca lista de casos). O codigo segue a terminologia TUSS: a TABELA 22 (procedimentos e
#: eventos em saude) usa codigos que comecam por 1 (consultas e visitas), 2 (clinicos), 3 (cirurgicos), 4
#: (diagnosticos e terapeuticos), 5 (atendimento por outros profissionais de saude) e 81-87 (odontologia); a
#: TABELA 18 (diarias, taxas e gases medicinais) comeca por 6; codigos que comecam por 9, 80, 88 e 89 sao
#: pacotes e taxas proprios do prestador; 15, 16 e 18 sao itens financeiros e agrupadores internos.
_PREFIXOS_ASSISTENCIAIS: Final[tuple[str, ...]] = (
    "10", "11", "12", "13", "14", "2", "3", "4", "5", "81", "82", "83", "84", "85", "86", "87",
)  # fmt: skip
#: GRUPOS do cadastro que sao de hotelaria, taxa ou insumo (qualquer prefixo).
_GRUPO_ADMINISTRATIVO: Final[re.Pattern[str]] = re.compile(
    r"^(?:taxa|aluguel|uti$|equipamentos|curativos|deslocamento|gases|individual$|coletivo$|item de"
    r"|processamento|alimentacao|diarias?\b)"
)
#: DESCRICAO de item administrativo (qualquer prefixo): taxa, aluguel, pacote, diaria, honorario, kit,
#: fornecimento, transporte/remocao em ambulancia, revisao ou laudo evolutivo "por procedimento", "a definir".
_DESCRICAO_ADMINISTRATIVA: Final[re.Pattern[str]] = re.compile(
    r"^(?:taxa|aluguel|pacote|pct|pcte|diaria|honorario|kit|fornecimento|material descartavel|transporte"
    r"|remocao em ambulancia|laudo evolutivo)\b"
    r"|\b(?:pacote|pct|pcte|aluguel)\b|\btaxa d[eao]\b|^(?:revisao|laudo)\b.*\bpor procedimento\b"
    r"|\ba definir\b"
)


def administrativo(descricao: str, grupo: str | None = None, codigo: str | None = None) -> bool:
    """O item NAO e' um procedimento assistencial (taxa, aluguel, pacote, diaria, revisao/laudo de outro
    exame, item financeiro). Regra de catalogo: tabela TUSS pelo codigo, grupo e descricao."""
    if codigo is not None and codigo.isdigit() and not codigo.startswith(_PREFIXOS_ASSISTENCIAIS):
        return True
    if grupo and _GRUPO_ADMINISTRATIVO.search(normalizar(grupo)):
        return True
    return bool(_DESCRICAO_ADMINISTRATIVA.search(normalizar(descricao)))


def assistencial(p: Procedimento) -> bool:
    return not administrativo(p.descricao, p.grupo, p.codigo) and not (
        p.nome_tuss and administrativo(p.nome_tuss)
    )


def grupos_do_termo(termo: str, sinonimos: Iterable[Sinonimo] = ()) -> list[set[str]]:
    """Uma alternativa por palavra do termo: a propria palavra e as palavras do nome OFICIAL dos sinonimos
    populares que a contem ("ultrassom" -> {ultrassom, ultrassonografia, us})."""
    plano = normalizar(termo)
    grupos: list[set[str]] = []
    for w in dict.fromkeys(palavras(termo)):
        alternativas = {w}
        for s in sinonimos:
            if any(
                w in p.split() and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", plano)
                for p in s.populares
            ):
                alternativas.update(palavras(s.oficial))
        grupos.append(alternativas)
    return grupos


def nota_lexica(
    termo: str,
    procedimento: Procedimento,
    peso: Mapping[str, float],
    grupos: Sequence[set[str]] | None = None,
) -> float:
    """Quanto as palavras do termo e do nome do procedimento se cobrem (ponderado por raridade): 70% o que do
    TERMO aparece no nome (cada palavra vale com qualquer das suas alternativas de sinonimo), 30% o que do
    NOME aparece no termo (o nome cheio de outras coisas perde)."""
    grupos = grupos if grupos is not None else [{w} for w in dict.fromkeys(palavras(termo))]
    alvo_lista = palavras(procedimento.documento())
    if not grupos or not alvo_lista:
        return 0.0
    alvo = set(alvo_lista)
    total = sum(max(peso.get(p, 1.0) for p in g) for g in grupos)
    achado = sum(max(peso.get(p, 1.0) for p in g) for g in grupos if any(_casa(p, alvo) for p in g))
    todas = set().union(*grupos)
    total_nome = sum(peso.get(p, 1.0) for p in alvo)
    achado_nome = sum(peso.get(p, 1.0) for p in alvo if _casa(p, todas))
    r = achado / total if total else 0.0
    q = achado_nome / total_nome if total_nome else 0.0
    return 0.7 * r + 0.3 * q


# --- Erro de digitacao: a palavra que nao existe no catalogo vira a mais parecida que existe -------------
def _distancia(a: str, b: str, limite: int) -> int:
    """Levenshtein com corte (acima de `limite` devolve `limite + 1`)."""
    if abs(len(a) - len(b)) > limite:
        return limite + 1
    anterior = list(range(len(b) + 1))
    for i, ca_ in enumerate(a, 1):
        atual = [i] + [0] * len(b)
        menor = atual[0]
        for j, cb in enumerate(b, 1):
            atual[j] = min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca_ != cb))
            menor = min(menor, atual[j])
        if menor > limite:
            return limite + 1
        anterior = atual
    return anterior[-1]


def corrigir_palavra(palavra: str, vocabulario: Mapping[str, int]) -> str | None:
    """A palavra do CATALOGO mais parecida com uma palavra que nao existe nele ("hemogama" -> "hemograma"):
    mesma primeira letra, ate' 1 erro (5 a 7 letras) ou 2 erros (8+); empate = a mais frequente. Regra geral
    de digitacao, sem lista de termos."""
    if len(palavra) < 5 or palavra in vocabulario or palavra.isdigit():
        return None
    limite = 1 if len(palavra) <= 7 else 2
    melhor: tuple[int, int, str] | None = None
    for candidata, vezes in vocabulario.items():
        if candidata[0] != palavra[0] or abs(len(candidata) - len(palavra)) > limite:
            continue
        d = _distancia(palavra, candidata, limite)
        if d <= limite and (melhor is None or (d, -vezes) < (melhor[0], melhor[1])):
            melhor = (d, -vezes, candidata)
    return melhor[2] if melhor is not None else None


class TransporteDeEmbeddings(Protocol):
    @property
    def modelo(self) -> str: ...

    async def vetores(self, textos: Sequence[str], *, consulta: bool) -> list[list[float]]: ...


@dataclass
class BuscaDeProcedimentos:
    """UMA por processo (composicao do receptor/simulador). Indice montado em segundo plano."""

    transporte: TransporteDeEmbeddings | None
    sinonimos: tuple[Sinonimo, ...] = ()
    relogio: Callable[[], float] = time.monotonic
    _itens: tuple[Procedimento, ...] = ()
    _versao: str | None = None
    _peso: dict[str, float] = field(default_factory=dict)
    _matriz: Any = None
    _versao_do_indice: str | None = None
    _montando: asyncio.Task[None] | None = None
    _falhou_em: float | None = None
    _fora_do_universo: int = 0
    _vocabulario: dict[str, int] = field(default_factory=dict)
    #: Os sinonimos da base de saude ja' foram somados (o grafo soma uma vez).
    com_base: bool = False

    @property
    def pronto(self) -> bool:
        return bool(self._itens)

    @property
    def com_significado(self) -> bool:
        return self._matriz is not None and self._versao_do_indice == self._versao

    def carregar(self, itens: Sequence[Procedimento], versao: str) -> None:
        """Troca o catalogo (mesma versao = nada muda) e agenda o indice por significado."""
        if versao == self._versao and self._itens:
            return
        unicos: dict[str, Procedimento] = {}
        fora = 0
        for p in itens:
            if not (p.codigo and p.descricao):
                continue
            if not assistencial(p):
                fora += 1
                continue
            unicos.setdefault(p.codigo, p)
        self._itens = tuple(unicos.values())
        self._fora_do_universo = fora
        self._versao = versao
        contagem: dict[str, int] = {}
        for p in self._itens:
            for w in set(palavras(p.documento())):
                contagem[w] = contagem.get(w, 0) + 1
        n = max(1, len(self._itens))
        import math

        self._peso = {w: 1.0 + math.log(n / c) for w, c in contagem.items()}
        self._vocabulario = {w: c for w, c in contagem.items() if len(w) >= 4 and not w.isdigit()}
        logger.info(
            "catalogo_procedimentos_carregado",
            itens=len(self._itens),
            administrativos=fora,
            versao=versao[:12],
        )

    def agendar_indice(self) -> None:
        if self.transporte is None or not self._itens or self.com_significado:
            return
        if self._falhou_em is not None and self.relogio() - self._falhou_em < _TEMPO_DE_ESPERA_APOS_FALHA_S:
            return
        if self._montando is None or self._montando.done():
            self._montando = asyncio.create_task(self._montar_indice())

    async def esperar_indice(self, prazo_s: float) -> bool:
        """Para quem PRECISA do indice antes de seguir (o roteiro do simulador)."""
        self.agendar_indice()
        if self._montando is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._montando), timeout=prazo_s)
            except TimeoutError:
                return False
        return self.com_significado

    async def _montar_indice(self) -> None:
        import numpy as np

        assert self.transporte is not None
        versao = self._versao
        itens = self._itens
        inicio = self.relogio()
        vetores: list[list[float]] = []
        try:
            for i in range(0, len(itens), LOTE_DO_INDICE):
                lote = [p.documento() for p in itens[i : i + LOTE_DO_INDICE]]
                vetores.extend(await self._lote_com_tentativas(lote))
                if i + LOTE_DO_INDICE < len(itens):
                    await asyncio.sleep(PAUSA_ENTRE_LOTES_S)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            self._falhou_em = self.relogio()
            logger.warning(
                "catalogo_procedimentos_indice_falhou", falha=type(exc).__name__, feitos=len(vetores)
            )
            return
        if versao != self._versao or len(vetores) != len(itens):
            return
        matriz = np.asarray(vetores, dtype=np.float32)
        normas = np.linalg.norm(matriz, axis=1, keepdims=True)
        normas[normas == 0] = 1.0
        self._matriz = matriz / normas
        self._versao_do_indice = versao
        self._falhou_em = None
        logger.info(
            "catalogo_procedimentos_indice",
            itens=len(itens),
            latencia_s=int(self.relogio() - inicio),
            modelo=self.transporte.modelo,
        )

    async def _lote_com_tentativas(self, lote: list[str]) -> list[list[float]]:
        """Um lote do indice com ate' 4 tentativas e espera crescente: dois processos montando o indice ao
        mesmo tempo estouram a cota por minuto do modelo, e isso nao pode derrubar o indice inteiro."""
        assert self.transporte is not None
        ultima = len(_ESPERAS_DO_LOTE_S) - 1
        for tentativa, espera in enumerate(_ESPERAS_DO_LOTE_S):
            try:
                return await self.transporte.vetores(lote, consulta=False)
            except PROGRAMMING_ERRORS:
                raise
            except EXTERNAL_DEPENDENCY_FAILURES:
                if tentativa == ultima:
                    raise
                await asyncio.sleep(espera)
        raise AssertionError("inalcancavel")  # pragma: no cover

    async def _significado(self, consulta: str) -> Any:
        if not self.com_significado or self.transporte is None:
            return None
        import numpy as np

        try:
            vetor = await asyncio.wait_for(
                self.transporte.vetores([consulta], consulta=True), timeout=PRAZO_CONSULTA_S
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            logger.warning("catalogo_procedimentos_consulta_falhou", falha=type(exc).__name__)
            return None
        q = np.asarray(vetor[0], dtype=np.float32)
        norma = float(np.linalg.norm(q)) or 1.0
        return self._matriz @ (q / norma)

    async def _notas(self, termo: str) -> tuple[list[tuple[float, float, float, int]], bool]:
        """(nota, significado, lexico, indice) de cada item, da maior nota para a menor; e se houve
        significado."""
        self.agendar_indice()
        termo = self.corrigir(termo)
        consulta = expandir(termo, self.sinonimos)
        notas_sem = await self._significado(consulta)
        grupos = grupos_do_termo(termo, self.sinonimos)
        lexicas = [nota_lexica(termo, p, self._peso, grupos) for p in self._itens]
        combinadas: list[tuple[float, float, float, int]] = []
        for i, lex in enumerate(lexicas):
            sem = float(notas_sem[i]) if notas_sem is not None else 0.0
            # Sem o significado, so' o lexico decide (com o mesmo limiar).
            nota = PESO_SIGNIFICADO * sem + (1 - PESO_SIGNIFICADO) * lex if notas_sem is not None else lex
            if _ADMINISTRATIVO.match(normalizar(self._itens[i].descricao)):
                nota *= PENALIDADE_ADMINISTRATIVO
            combinadas.append((nota, sem, lex, i))
        combinadas.sort(key=lambda t: (-t[0], self._itens[t[3]].codigo))
        return combinadas, notas_sem is not None

    def lexico(self, termo: str, procedimento: Procedimento) -> float:
        """A nota lexica do termo (corrigido e com sinonimos) contra um procedimento."""
        if not termo or not self._itens:
            return 0.0
        termo = self.corrigir(termo)
        return nota_lexica(termo, procedimento, self._peso, grupos_do_termo(termo, self.sinonimos))

    def corrigir(self, termo: str) -> str:
        """O termo com cada palavra que nao existe no catalogo trocada pela mais parecida que existe."""
        saida: list[str] = []
        trocou = False
        for w in normalizar(termo).split():
            certa = corrigir_palavra(w, self._vocabulario) if w not in _PALAVRAS_VAZIAS else None
            saida.append(certa or w)
            trocou = trocou or certa is not None
        if trocou:
            logger.info("catalogo_procedimentos_digitacao_corrigida")
            return " ".join(saida)
        return termo

    async def recuperar(self, termo: str, n: int = MAX_RECUPERADOS) -> tuple[Candidato, ...]:
        """Os ~`n` candidatos para a ESCOLHA do modelo: os melhores pela nota combinada, mais os melhores so'
        pelo significado e so' pelo lexico (um nome popular pode ir bem num e mal no outro). Sem repetir
        nome."""
        if not self._itens or not normalizar(termo):
            return ()
        combinadas, _ = await self._notas(termo)
        por_sem = sorted(combinadas, key=lambda t: -t[1])[: n // 4]
        por_lex = sorted(combinadas, key=lambda t: -t[2])[: n // 4]
        saida: list[Candidato] = []
        vistos: set[str] = set()
        for nota, sem, lex, i in [*combinadas[:n], *por_sem, *por_lex]:
            p = self._itens[i]
            chave = normalizar(p.nome)
            if chave in vistos or (lex == 0.0 and sem < 0.2):
                continue
            vistos.add(chave)
            saida.append(Candidato(p, round(nota, 4), round(sem, 4), round(lex, 4)))
        logger.info("catalogo_procedimentos_recuperados", candidatos=len(saida))
        return tuple(saida)

    async def buscar(self, termo: str) -> Resultado:
        """Ate' 3 candidatos para o termo (ja' MINIMIZADO), com a decisao de "certo" (reserva sem o
        modelo)."""
        if not self._itens or not normalizar(termo):
            return Resultado((), False, False)
        combinadas, com_sem = await self._notas(termo)
        notas_sem = True if com_sem else None
        candidatos: list[Candidato] = []
        vistos: set[str] = set()
        for nota, sem, lex, i in combinadas:
            if nota < NOTA_MINIMA or len(candidatos) >= MAX_CANDIDATOS:
                break
            p = self._itens[i]
            chave = normalizar(p.nome)
            if chave in vistos:
                continue
            vistos.add(chave)
            candidatos.append(Candidato(p, round(nota, 4), round(sem, 4), round(lex, 4)))
        certo = (
            bool(candidatos)
            and candidatos[0].nota >= NOTA_CERTA
            and (len(candidatos) == 1 or candidatos[0].nota - candidatos[1].nota >= FOLGA_CERTA)
        )
        logger.info(
            "catalogo_procedimentos_busca",
            candidatos=len(candidatos),
            certo=certo,
            melhor=candidatos[0].nota if candidatos else None,
            com_significado=notas_sem is not None,
        )
        return Resultado(tuple(candidatos), certo, notas_sem is not None)


__all__ = [
    "FOLGA_CERTA",
    "MAX_CANDIDATOS",
    "MAX_RECUPERADOS",
    "NOTA_CERTA",
    "NOTA_MINIMA",
    "BuscaDeProcedimentos",
    "Candidato",
    "Procedimento",
    "Resultado",
    "Sinonimo",
    "administrativo",
    "assistencial",
    "carregar_sinonimos",
    "corrigir_palavra",
    "expandir",
    "grupos_do_termo",
    "nota_lexica",
    "normalizar",
    "palavras",
    "raiz_dos_sinonimos",
    "sinonimos_da_base",
]
