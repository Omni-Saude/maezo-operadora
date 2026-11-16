"""O LEITOR do pacote K2 do `regras-contratos-operadora` (rco) para a camada `regra_geral` (HEL-3a).

O K2 e' o Manual do Beneficiario gerado das regras de negocio do rco, em linguagem simples: um indice por
regra (`search-index.json`) e o capitulo geral ("o que vale para todos os temas", de
`templates/manual/capitulo-geral.yaml`). Este modulo so' LE e VERIFICA o pacote publicado pelo fornecedor:
nao guarda conteudo proprio, nao e' base de conhecimento paralela (DL-0167). Interface entre repositorios do
projeto: artefato versionado com hash, publicado pelo fornecedor e PINADO pelo consumidor (`PinDoK2`).

FORMATO ESPERADO `rco-k2-v1` (a NOSSA especificacao, AINDA NAO CONFIRMADA pelo rco; a fixture dos testes
e' sintetica). O diretorio do pacote contem EXATAMENTE estes tres arquivos:

- `manifesto.json`::

      {"formato": "rco-k2-v1", "versao": str, "commit_rco": str (40 hex),
       "gerado_em": str (ISO-8601 UTC), "ratificado": bool,
       "arquivos": {"search-index.json": "sha256:<hex>", "capitulo-geral.yaml": "sha256:<hex>"}}

  O digest de cada arquivo vai na notacao `sha256:<64 hex>` (a mesma das imagens OCI): o hex cru ao lado do
  nome do arquivo e' lido como chave de API pela regra `generic-api-key` do gitleaks.

- `search-index.json`: lista de regras
  `{"id": str, "url": str, "titulo": str, "resumo": str, "texto": str, "sinonimos": [str, ...]}`;
- `capitulo-geral.yaml`: `{"secoes": [{"id": str, "titulo": str, "texto": str}, ...]}`.

O pin (`PinDoK2`) e' a versao e o sha256 dos BYTES do `manifesto.json`. Chaves que comecam com `_` (ex.:
`"_amostra"`) sao anotacoes do publicador e sao IGNORADAS em qualquer nivel; qualquer outra chave fora do
formato e' recusada (formato fechado: campo novo do rco = versao nova do formato).

VERIFICACAO (falha fechada; `PacoteK2InvalidoError(motivo)`, motivo curto e NUNCA conteudo), nesta ordem:
diretorio ausente; manifesto ausente, grande demais ou ilegivel; `formato` diferente; `versao` diferente do
pin; sha256 do manifesto diferente do pin; `ratificado` nao e' `true`; `commit_rco`/`gerado_em`/`arquivos`
fora do formato; entrada do diretorio nao listada no manifesto (inclusive oculta ou subdiretorio); arquivo
listado ausente, nao regular (link), grande demais ou com sha256 diferente; JSON/YAML invalido
(`yaml.safe_load`); campo obrigatorio ausente, vazio ou de tipo errado; id de regra ou de secao duplicado;
indice sem regra. Limite de `TAMANHO_MAXIMO` bytes por arquivo, checado ANTES de ler.

BUSCA (`ManualK2.buscar`): casamento EXATO, sem similaridade aproximada. Normalizar = NFKD, sem marcas
combinantes (acentos), minusculas (`casefold`), tokens `[a-z0-9]+` unidos por um espaco. As CHAVES de uma
regra sao: cada sinonimo normalizado INTEIRO (a frase toda, ex. "exame de imagem") e cada token do titulo
normalizado com 3 ou mais caracteres (tokens curtos como "de", "a" nao casam). Um termo casa a regra se o
termo normalizado e' IGUAL a uma chave: sem prefixo, sem plural, sem pedaco de frase. O resultado e' a
uniao dos termos, sem repeticao, em ordem de id.

`RegraDoManual.url` e' METADADO DE AUDITORIA (onde a regra mora no site do manual): nunca vai ao texto da
resposta; so' a habilidade decide o que a pessoa le'.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import structlog
import yaml

logger = structlog.get_logger(__name__)

FORMATO: Final[str] = "rco-k2-v1"
MANIFESTO: Final[str] = "manifesto.json"
INDICE: Final[str] = "search-index.json"
CAPITULO_GERAL: Final[str] = "capitulo-geral.yaml"
#: Os arquivos que o manifesto lista (nem mais, nem menos).
ARQUIVOS_DO_PACOTE: Final[frozenset[str]] = frozenset({INDICE, CAPITULO_GERAL})
#: Teto por arquivo: o pacote real tem algumas centenas de regras; acima disso e' lixo ou erro de publicacao.
TAMANHO_MAXIMO: Final[int] = 20 * 1024 * 1024
#: Tokens do titulo mais curtos que isto nao viram chave de busca (artigos e preposicoes).
_TOKEN_MINIMO_DO_TITULO: Final[int] = 3

_HEX40: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_DIGEST: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")
_PREFIXO_DO_DIGEST: Final[str] = "sha256:"
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_CAMPOS_DO_MANIFESTO: Final[frozenset[str]] = frozenset(
    {"formato", "versao", "commit_rco", "gerado_em", "ratificado", "arquivos"}
)
_CAMPOS_DA_REGRA: Final[frozenset[str]] = frozenset({"id", "url", "titulo", "resumo", "texto", "sinonimos"})
_CAMPOS_DA_SECAO: Final[frozenset[str]] = frozenset({"id", "titulo", "texto"})


class PacoteK2InvalidoError(Exception):
    """O pacote nao passou na verificacao. `motivo` e' um codigo curto (vai a log), nunca conteudo."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True, slots=True)
class PinDoK2:
    """O pin do consumidor: a versao do pacote e o sha256 (hex) dos BYTES do `manifesto.json`."""

    versao: str
    sha256_manifesto: str


@dataclass(frozen=True, slots=True)
class RegraDoManual:
    """Uma regra do indice. `url` e' metadado de auditoria: nunca vai ao texto da resposta."""

    id: str
    url: str
    titulo: str
    resumo: str
    texto: str
    sinonimos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SecaoGeral:
    """Uma secao do capitulo geral ("o que vale para todos os temas")."""

    id: str
    titulo: str
    texto: str


def normalizar(termo: str) -> str:
    """Minusculas, sem acento, tokens `[a-z0-9]+` unidos por um espaco ("Autorização  Prévia" -> "autorizacao
    previa"). E' a UNICA normalizacao da busca."""
    decomposto = unicodedata.normalize("NFKD", termo)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()
    return " ".join(_TOKEN.findall(sem_acento))


def _chaves_da_regra(regra: RegraDoManual) -> frozenset[str]:
    chaves = {normalizar(s) for s in regra.sinonimos}
    chaves.update(t for t in normalizar(regra.titulo).split() if len(t) >= _TOKEN_MINIMO_DO_TITULO)
    chaves.discard("")
    return frozenset(chaves)


@dataclass(frozen=True, slots=True)
class ManualK2:
    """O pacote K2 verificado. Construa com `carregar` (a verificacao mora la')."""

    versao: str
    commit_rco: str
    regras: Mapping[str, RegraDoManual]
    capitulo_geral: tuple[SecaoGeral, ...]
    #: O sha256 dos bytes do manifesto, ja' conferido contra o pin (`carregar`); vai a `Fonte` da resposta.
    sha256_manifesto: str = ""
    #: chave de busca normalizada -> ids de regra em ordem (derivado de `regras`).
    _por_chave: Mapping[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        por_chave: dict[str, list[str]] = {}
        for id_ in sorted(self.regras):
            for chave in _chaves_da_regra(self.regras[id_]):
                por_chave.setdefault(chave, []).append(id_)
        object.__setattr__(
            self, "_por_chave", MappingProxyType({k: tuple(v) for k, v in sorted(por_chave.items())})
        )

    def regra(self, id_: str) -> RegraDoManual | None:
        """A regra pelo id exato, ou `None`."""
        return self.regras.get(id_)

    def buscar(self, termos: Sequence[str]) -> tuple[RegraDoManual, ...]:
        """As regras cujas chaves casam EXATAMENTE algum termo normalizado (regra no docstring do modulo)."""
        ids: set[str] = set()
        for termo in termos:
            chave = normalizar(termo)
            if chave:
                ids.update(self._por_chave.get(chave, ()))
        return tuple(self.regras[i] for i in sorted(ids))

    def sinonimos_por_termo(self) -> Mapping[str, tuple[str, ...]]:
        """Termo popular normalizado (dos `sinonimos`, nao do titulo) -> ids de regra, em ordem de id."""
        por_termo: dict[str, set[str]] = {}
        for regra in self.regras.values():
            for sinonimo in regra.sinonimos:
                termo = normalizar(sinonimo)
                if termo:
                    por_termo.setdefault(termo, set()).add(regra.id)
        return MappingProxyType({t: tuple(sorted(ids)) for t, ids in sorted(por_termo.items())})


# --- Leitura e verificacao ---------------------------------------------------------------------------------
def _ler(caminho: Path, *, ausente: str) -> bytes:
    if caminho.is_symlink() or not caminho.exists():
        raise PacoteK2InvalidoError(ausente if not caminho.is_symlink() else "arquivo_nao_regular")
    if not caminho.is_file():
        raise PacoteK2InvalidoError("arquivo_nao_regular")
    if caminho.stat().st_size > TAMANHO_MAXIMO:
        raise PacoteK2InvalidoError("arquivo_grande_demais")
    dados = caminho.read_bytes()
    if len(dados) > TAMANHO_MAXIMO:  # o arquivo cresceu entre o stat e a leitura
        raise PacoteK2InvalidoError("arquivo_grande_demais")
    return dados


def _sem_anotacoes(bruto: Mapping[str, Any], permitidos: frozenset[str], *, motivo: str) -> dict[str, Any]:
    """Tira as chaves `_...` e recusa qualquer outra chave fora do formato."""
    limpo = {k: v for k, v in bruto.items() if not (isinstance(k, str) and k.startswith("_"))}
    if not set(limpo) <= permitidos:
        raise PacoteK2InvalidoError(motivo)
    return limpo


def _texto(valor: object, *, motivo: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise PacoteK2InvalidoError(motivo)
    return valor


def _verificar_manifesto(dados: bytes, pin: PinDoK2) -> tuple[str, str, dict[str, str]]:
    """O manifesto -> `(versao, commit_rco, {arquivo: sha256})`, na ordem do docstring do modulo."""
    try:
        bruto = json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PacoteK2InvalidoError("manifesto_ilegivel") from None
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("manifesto_ilegivel")
    if bruto.get("formato") != FORMATO:
        raise PacoteK2InvalidoError("formato_diferente")
    if not isinstance(bruto.get("versao"), str) or bruto["versao"] != pin.versao:
        raise PacoteK2InvalidoError("versao_diferente_do_pin")
    if hashlib.sha256(dados).hexdigest() != pin.sha256_manifesto.lower():
        raise PacoteK2InvalidoError("sha256_do_manifesto_diferente_do_pin")
    if bruto.get("ratificado") is not True:
        raise PacoteK2InvalidoError("pacote_nao_ratificado")
    manifesto = _sem_anotacoes(bruto, _CAMPOS_DO_MANIFESTO, motivo="manifesto_com_campo_desconhecido")
    if not set(manifesto) >= _CAMPOS_DO_MANIFESTO:
        raise PacoteK2InvalidoError("manifesto_sem_campo_obrigatorio")
    commit = manifesto["commit_rco"]
    if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
        raise PacoteK2InvalidoError("commit_rco_invalido")
    if not _e_instante_utc(manifesto["gerado_em"]):
        raise PacoteK2InvalidoError("gerado_em_invalido")
    arquivos = manifesto["arquivos"]
    if not isinstance(arquivos, dict) or set(arquivos) != ARQUIVOS_DO_PACOTE:
        raise PacoteK2InvalidoError("lista_de_arquivos_invalida")
    for hash_ in arquivos.values():
        if not isinstance(hash_, str) or not _DIGEST.fullmatch(hash_):
            raise PacoteK2InvalidoError("lista_de_arquivos_invalida")
    return manifesto["versao"], commit, dict(arquivos)


def _e_instante_utc(valor: object) -> bool:
    if not isinstance(valor, str) or "T" not in valor:
        return False
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return False
    return instante.tzinfo is not None and instante.utcoffset() == timedelta(0)


def _verificar_diretorio(diretorio: Path, listados: Mapping[str, str]) -> dict[str, bytes]:
    """Nenhuma entrada fora do manifesto; cada arquivo listado presente e com o sha256 do manifesto."""
    esperados = {MANIFESTO, *listados}
    for entrada in diretorio.iterdir():
        if entrada.name not in esperados:
            raise PacoteK2InvalidoError("arquivo_nao_listado_no_manifesto")
    conteudos: dict[str, bytes] = {}
    for nome, hash_ in listados.items():
        dados = _ler(diretorio / nome, ausente="arquivo_listado_ausente")
        if _PREFIXO_DO_DIGEST + hashlib.sha256(dados).hexdigest() != hash_:
            raise PacoteK2InvalidoError("sha256_de_arquivo_diferente")
        conteudos[nome] = dados
    return conteudos


def _regras(dados: bytes) -> dict[str, RegraDoManual]:
    try:
        bruto = json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PacoteK2InvalidoError("indice_json_invalido") from None
    if not isinstance(bruto, list):
        raise PacoteK2InvalidoError("indice_fora_do_formato")
    if not bruto:
        raise PacoteK2InvalidoError("indice_sem_regras")
    regras: dict[str, RegraDoManual] = {}
    for item in bruto:
        if not isinstance(item, dict):
            raise PacoteK2InvalidoError("regra_fora_do_formato")
        campos = _sem_anotacoes(item, _CAMPOS_DA_REGRA, motivo="regra_com_campo_desconhecido")
        if not set(campos) >= _CAMPOS_DA_REGRA:
            raise PacoteK2InvalidoError("regra_sem_campo_obrigatorio")
        sinonimos = campos["sinonimos"]
        if not isinstance(sinonimos, list):
            raise PacoteK2InvalidoError("regra_fora_do_formato")
        regra = RegraDoManual(
            id=_texto(campos["id"], motivo="regra_sem_id"),
            url=_texto(campos["url"], motivo="regra_fora_do_formato"),
            titulo=_texto(campos["titulo"], motivo="regra_fora_do_formato"),
            resumo=_texto(campos["resumo"], motivo="regra_sem_resumo"),
            texto=_texto(campos["texto"], motivo="regra_sem_texto"),
            sinonimos=tuple(_texto(s, motivo="regra_fora_do_formato") for s in sinonimos),
        )
        if regra.id in regras:
            raise PacoteK2InvalidoError("id_de_regra_duplicado")
        regras[regra.id] = regra
    return regras


def _capitulo_geral(dados: bytes) -> tuple[SecaoGeral, ...]:
    try:
        bruto = yaml.safe_load(dados.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        raise PacoteK2InvalidoError("capitulo_geral_yaml_invalido") from None
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("capitulo_geral_fora_do_formato")
    raiz = _sem_anotacoes(bruto, frozenset({"secoes"}), motivo="capitulo_geral_com_campo_desconhecido")
    secoes_brutas = raiz.get("secoes")
    if not isinstance(secoes_brutas, list) or not secoes_brutas:
        raise PacoteK2InvalidoError("capitulo_geral_fora_do_formato")
    secoes: list[SecaoGeral] = []
    for item in secoes_brutas:
        if not isinstance(item, dict):
            raise PacoteK2InvalidoError("secao_fora_do_formato")
        campos = _sem_anotacoes(item, _CAMPOS_DA_SECAO, motivo="secao_com_campo_desconhecido")
        if not set(campos) >= _CAMPOS_DA_SECAO:
            raise PacoteK2InvalidoError("secao_sem_campo_obrigatorio")
        secoes.append(
            SecaoGeral(
                id=_texto(campos["id"], motivo="secao_fora_do_formato"),
                titulo=_texto(campos["titulo"], motivo="secao_fora_do_formato"),
                texto=_texto(campos["texto"], motivo="secao_sem_texto"),
            )
        )
    if len({s.id for s in secoes}) != len(secoes):
        raise PacoteK2InvalidoError("id_de_secao_duplicado")
    return tuple(secoes)


def carregar(diretorio: Path, *, pin: PinDoK2) -> ManualK2:
    """Le' e verifica o pacote K2 contra o pin. Qualquer desvio -> `PacoteK2InvalidoError` (falha fechada).

    `OSError` de leitura (permissao, disco) sobe como esta; `carregar_ou_nenhum` trata os dois."""
    if not diretorio.is_dir():
        raise PacoteK2InvalidoError("diretorio_ausente")
    dados_do_manifesto = _ler(diretorio / MANIFESTO, ausente="manifesto_ausente")
    versao, commit_rco, listados = _verificar_manifesto(dados_do_manifesto, pin)
    conteudos = _verificar_diretorio(diretorio, listados)
    regras = _regras(conteudos[INDICE])
    return ManualK2(
        versao=versao,
        commit_rco=commit_rco,
        regras=MappingProxyType(dict(sorted(regras.items()))),
        capitulo_geral=_capitulo_geral(conteudos[CAPITULO_GERAL]),
        sha256_manifesto=pin.sha256_manifesto,
    )


def carregar_ou_nenhum(diretorio: Path | None, *, pin: PinDoK2 | None) -> ManualK2 | None:
    """`carregar`, ou `None` (a habilidade cai no aviso de canais) com o log `helena_k2_recusado` so' com o
    motivo: sem diretorio, sem pin, pacote invalido ou erro de leitura."""
    if diretorio is None or pin is None:
        logger.warning("helena_k2_recusado", motivo="sem_diretorio" if diretorio is None else "sem_pin")
        return None
    try:
        return carregar(diretorio, pin=pin)
    except PacoteK2InvalidoError as exc:
        # So' o token de classe no log (cerca LUC-06/NEW-01): o motivo exato e' o de `carregar`, que o teste
        # e o operador obtem chamando-o direto sobre o mesmo pacote.
        logger.warning("helena_k2_recusado", motivo="pacote_invalido", erro=type(exc).__name__)
    except OSError as exc:
        logger.warning("helena_k2_recusado", motivo="erro_de_leitura", erro=type(exc).__name__)
    return None


__all__ = [
    "ARQUIVOS_DO_PACOTE",
    "CAPITULO_GERAL",
    "FORMATO",
    "INDICE",
    "MANIFESTO",
    "TAMANHO_MAXIMO",
    "ManualK2",
    "PacoteK2InvalidoError",
    "PinDoK2",
    "RegraDoManual",
    "SecaoGeral",
    "carregar",
    "carregar_ou_nenhum",
    "normalizar",
]
