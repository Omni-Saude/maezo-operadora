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
NOTA_MINIMA: Final[float] = 0.42
#: Nota e folga sobre o segundo para o primeiro ser escolhido SEM perguntar.
NOTA_CERTA: Final[float] = 0.70
FOLGA_CERTA: Final[float] = 0.08
#: Peso do significado na nota combinada (o resto e' o lexico).
PESO_SIGNIFICADO: Final[float] = 0.6
MAX_CANDIDATOS: Final[int] = 3
PRAZO_CONSULTA_S: Final[float] = 4.0
#: O catalogo inteiro (~10 mil itens) em lotes, com ritmo para nao estourar a cota por minuto do modelo.
LOTE_DO_INDICE: Final[int] = 200
PAUSA_ENTRE_LOTES_S: Final[float] = 2.5
_TEMPO_DE_ESPERA_APOS_FALHA_S: Final[float] = 300.0

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
    return Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "cobertura" / "sinonimos.yaml"


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
def _casa(palavra: str, alvo: set[str]) -> bool:
    if palavra in alvo:
        return True
    if len(palavra) >= 5:
        raiz = palavra[:5]
        return any(a.startswith(raiz) for a in alvo if len(a) >= 5)
    return False


def nota_lexica(termo: str, procedimento: Procedimento, peso: Mapping[str, float]) -> float:
    """Fracao (ponderada por raridade) das palavras do termo que aparecem no nome do procedimento."""
    termos = palavras(termo)
    if not termos:
        return 0.0
    alvo = set(palavras(procedimento.documento()))
    total = sum(peso.get(p, 1.0) for p in termos)
    achado = sum(peso.get(p, 1.0) for p in termos if _casa(p, alvo))
    return achado / total if total else 0.0


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
        for p in itens:
            if p.codigo and p.descricao:
                unicos.setdefault(p.codigo, p)
        self._itens = tuple(unicos.values())
        self._versao = versao
        contagem: dict[str, int] = {}
        for p in self._itens:
            for w in set(palavras(p.documento())):
                contagem[w] = contagem.get(w, 0) + 1
        n = max(1, len(self._itens))
        import math

        self._peso = {w: 1.0 + math.log(n / c) for w, c in contagem.items()}
        logger.info("catalogo_procedimentos_carregado", itens=len(self._itens), versao=versao[:12])

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
                vetores.extend(await self.transporte.vetores(lote, consulta=False))
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

    async def buscar(self, termo: str) -> Resultado:
        """Ate' 3 candidatos para o termo (ja' MINIMIZADO), com a decisao de "certo"."""
        if not self._itens or not normalizar(termo):
            return Resultado((), False, False)
        self.agendar_indice()
        consulta = expandir(termo, self.sinonimos)
        notas_sem = await self._significado(consulta)
        lexicas = [nota_lexica(consulta, p, self._peso) for p in self._itens]
        combinadas: list[tuple[float, float, float, int]] = []
        for i, lex in enumerate(lexicas):
            sem = float(notas_sem[i]) if notas_sem is not None else 0.0
            # Sem o significado, so' o lexico decide (com o mesmo limiar).
            nota = PESO_SIGNIFICADO * sem + (1 - PESO_SIGNIFICADO) * lex if notas_sem is not None else lex
            combinadas.append((nota, sem, lex, i))
        combinadas.sort(key=lambda t: (-t[0], self._itens[t[3]].codigo))
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
    "NOTA_CERTA",
    "NOTA_MINIMA",
    "BuscaDeProcedimentos",
    "Candidato",
    "Procedimento",
    "Resultado",
    "Sinonimo",
    "carregar_sinonimos",
    "expandir",
    "nota_lexica",
    "normalizar",
    "palavras",
    "raiz_dos_sinonimos",
    "sinonimos_da_base",
]
