"""O LEITOR do pacote K2 do `regras-contratos-operadora` (rco) para a camada `regra_geral` (HEL-3a, HEL-3c).

O K2 e' o Manual do Beneficiario gerado das regras de negocio do rco, em linguagem simples. Este modulo so'
LE e VERIFICA o pacote publicado pelo fornecedor: nao guarda conteudo proprio, nao e' base de conhecimento
paralela (DL-0167). Interface entre repositorios do projeto: artefato versionado com hash, publicado pelo
fornecedor e PINADO pelo consumidor (`PinDoK2`, lido do lock do consumidor por `pin_do_lock`).

CONTRATO `rco-k2-v1` (rco main fe1dc89, `docs/programa/contrato-k2.md`). Todo digest e'
`sha256:<64 hex minusculo>`, nunca o hex cru (o hex cru ao lado do nome do arquivo e' lido como chave de API
pela regra `generic-api-key` do gitleaks). O diretorio do pacote contem EXATAMENTE estes sete arquivos:

- `manifesto.json`: bytes CANONICOS (JSON UTF-8, chaves ordenadas, `ensure_ascii=False`, separadores
  `(",", ":")`, sem quebra de linha no fim) com EXATAMENTE::

      {"formato": "rco-k2-v1", "versao": str, "commit_rco": str (40 hex minusculo),
       "gerado_em": "AAAA-MM-DDTHH:MM:SSZ", "ratificado": bool,
       "arquivos": {<os 5 arquivos de dados>: "sha256:<hex>"}}

- `manifesto.sha256`: EXATAMENTE `sha256:<hex dos bytes do manifesto.json>` e UMA quebra de linha;
- `search-index.json`: `{"versao_manual", "data_versao", "publico": "beneficiario", "sinonimos",
  "documentos"}`; `versao_manual` igual a `versao` do manifesto; `sinonimos` igual ao `sinonimos.json`; cada
  documento com EXATAMENTE `id, titulo, versao, publico ("beneficiario"), publico_nome, dominio,
  dominio_nome, resumo, url, texto, sinonimos (lista ordenada; pode ser vazia), t_titulo, t_chaves, t_texto`
  (os `t_*` sao TEXTO: palavras normalizadas separadas por espaco; podem ser vazios);
- `capitulo-geral.yaml`: `{"secoes": [{"id", "titulo", "texto"}, ...]}` (nao vazia, ids unicos);
- `sinonimos.json`: `{termo popular normalizado: [termo tecnico normalizado, ...]}`;
- `sinonimos-por-termo.json`: `{termo popular: [id de regra, ...]}` (listas podem ser vazias), com as MESMAS
  chaves do `sinonimos.json`; o `sinonimos` de cada documento e' a projecao inversa (os termos cuja lista
  contem o id do documento, ordenados);
- `avaliacoes.yaml`: `{"avaliacoes": [{"id", "publico" ("beneficiario"), "pergunta", "resposta_esperada",
  "elementos_obrigatorios", "o_que_seria_invencao", "regra_origem" (ids existentes; pode ser vazia),
  opcional "comportamento"}, ...]}` (a lista pode ser vazia; ids unicos).

Formato FECHADO em todo nivel: campo desconhecido ou ausente recusa o pacote (campo novo do rco = versao nova
do formato). Nao ha' tolerancia a anotacoes com `_`.

RATIFICACAO: a ratificacao e' ato humano no rco. O padrao RECUSA pacote com `ratificado: false`; so'
`aceitar_nao_ratificado=True` o aceita, e esse booleano vem de `aceita_nao_ratificado` (decisao da ROOT):
PRODUCAO NUNCA aceita, ambiente desconhecido ou vazio tambem nao (falha fechada); em dev vale a configuracao
nomeada `CHAVE_ACEITAR_NAO_RATIFICADO`.

VERIFICACAO (falha fechada; `PacoteK2InvalidoError(motivo)`, motivo curto e NUNCA conteudo), nesta ordem:
diretorio ausente; manifesto ausente, grande demais ou ilegivel; bytes nao canonicos; `formato` diferente;
`versao` diferente do pin; sha256 do manifesto diferente do pin; `manifesto.sha256` ausente, fora do formato
ou diferente; campo do manifesto desconhecido ou ausente; `ratificado` nao booleano ou falso sem aceite;
`commit_rco`/`gerado_em`/`arquivos` fora do formato; entrada do diretorio fora dos sete (inclusive oculta ou
subdiretorio); arquivo listado ausente, nao regular (link), grande demais ou com sha256 diferente; o conteudo
de cada arquivo (JSON/YAML invalido, `yaml.safe_load`; campo ausente, vazio, desconhecido ou de tipo errado;
publico diferente; id duplicado ou desconhecido; inconsistencia entre indice e sinonimos). Limite de
`TAMANHO_MAXIMO` bytes por arquivo, checado ANTES de ler.

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
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import structlog
import yaml

logger = structlog.get_logger(__name__)

FORMATO: Final[str] = "rco-k2-v1"
MANIFESTO: Final[str] = "manifesto.json"
MANIFESTO_SHA256: Final[str] = "manifesto.sha256"
INDICE: Final[str] = "search-index.json"
CAPITULO_GERAL: Final[str] = "capitulo-geral.yaml"
SINONIMOS: Final[str] = "sinonimos.json"
SINONIMOS_POR_TERMO: Final[str] = "sinonimos-por-termo.json"
AVALIACOES: Final[str] = "avaliacoes.yaml"
#: Os arquivos de dados que o manifesto lista (nem mais, nem menos).
ARQUIVOS_DO_PACOTE: Final[frozenset[str]] = frozenset(
    {INDICE, CAPITULO_GERAL, SINONIMOS, SINONIMOS_POR_TERMO, AVALIACOES}
)
#: Tudo o que o diretorio do pacote pode ter (e tem de ter).
ENTRADAS_DO_DIRETORIO: Final[frozenset[str]] = ARQUIVOS_DO_PACOTE | {MANIFESTO, MANIFESTO_SHA256}
PUBLICO: Final[str] = "beneficiario"
COMPORTAMENTOS: Final[frozenset[str]] = frozenset(
    {"corrige_premissa", "declara_ausencia", "identifica_publico"}
)
#: Teto por arquivo: o pacote real tem algumas centenas de regras; acima disso e' lixo ou erro de publicacao.
TAMANHO_MAXIMO: Final[int] = 20 * 1024 * 1024
#: Tokens do titulo mais curtos que isto nao viram chave de busca (artigos e preposicoes).
_TOKEN_MINIMO_DO_TITULO: Final[int] = 3

#: A configuracao NOMEADA que pede aceitar pacote nao ratificado (testes, evals, dev). Nunca vale em producao.
CHAVE_ACEITAR_NAO_RATIFICADO: Final[str] = "cobertura_k2_aceitar_nao_ratificado"
#: A variavel com o nome do ambiente, pelo nome que o resto do repo usa (`local.env` do deploy: dev, prod).
VARIAVEL_DO_AMBIENTE: Final[str] = "MAEZO_ENV"
#: Os UNICOS ambientes em que a configuracao vale (lista de permissao: producao, ambiente desconhecido e
#: vazio ficam de fora por construcao).
AMBIENTES_QUE_PODEM_ACEITAR: Final[frozenset[str]] = frozenset({"dev"})

FORMATO_DO_LOCK: Final[Mapping[str, str]] = MappingProxyType({"name": "rco-k2-lock", "version": "1.0.0"})

_HEX40: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
_DIGEST: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")
_INSTANTE_UTC: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PREFIXO_DO_DIGEST: Final[str] = "sha256:"
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_CAMPOS_DO_MANIFESTO: Final[frozenset[str]] = frozenset(
    {"formato", "versao", "commit_rco", "gerado_em", "ratificado", "arquivos"}
)
_CAMPOS_DO_INDICE: Final[frozenset[str]] = frozenset(
    {"versao_manual", "data_versao", "publico", "sinonimos", "documentos"}
)
_CAMPOS_DA_REGRA: Final[frozenset[str]] = frozenset(
    {
        "id", "titulo", "versao", "publico", "publico_nome", "dominio", "dominio_nome", "resumo", "url",
        "texto", "sinonimos", "t_titulo", "t_chaves", "t_texto",
    }
)  # fmt: skip
_CAMPOS_DA_SECAO: Final[frozenset[str]] = frozenset({"id", "titulo", "texto"})
_CAMPOS_DA_AVALIACAO: Final[frozenset[str]] = frozenset(
    {
        "id", "publico", "pergunta", "resposta_esperada", "elementos_obrigatorios", "o_que_seria_invencao",
        "regra_origem",
    }
)  # fmt: skip
_CAMPOS_OPCIONAIS_DA_AVALIACAO: Final[frozenset[str]] = frozenset({"comportamento"})
_CAMPOS_DO_LOCK: Final[frozenset[str]] = frozenset(
    {"lock_format", "estado", "provenance", "manifest_pin", "versao"}
)


class PacoteK2InvalidoError(Exception):
    """O pacote nao passou na verificacao. `motivo` e' um codigo curto (vai a log), nunca conteudo."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True, slots=True)
class PinDoK2:
    """O pin do consumidor: a versao do pacote e o sha256 (64 hex, SEM o prefixo `sha256:`) dos BYTES do
    `manifesto.json`."""

    versao: str
    sha256_manifesto: str


@dataclass(frozen=True, slots=True)
class RegraDoManual:
    """Um documento do indice. `url` e' metadado de auditoria: nunca vai ao texto da resposta."""

    id: str
    url: str
    titulo: str
    resumo: str
    texto: str
    sinonimos: tuple[str, ...]
    versao: str
    dominio: str
    dominio_nome: str


@dataclass(frozen=True, slots=True)
class SecaoGeral:
    """Uma secao do capitulo geral ("o que vale para todos os temas")."""

    id: str
    titulo: str
    texto: str


@dataclass(frozen=True, slots=True)
class AvaliacaoDoManual:
    """Uma pergunta de avaliacao do `avaliacoes.yaml` (dado puro: alimenta os golden evals)."""

    id: str
    publico: str
    pergunta: str
    resposta_esperada: str
    elementos_obrigatorios: tuple[str, ...]
    o_que_seria_invencao: tuple[str, ...]
    regra_origem: tuple[str, ...]
    comportamento: str | None = None


def normalizar(termo: str) -> str:
    """Minusculas, sem acento, tokens `[a-z0-9]+` unidos por um espaco ("Autorização  Prévia" -> "autorizacao
    previa"). E' a UNICA normalizacao da busca."""
    decomposto = unicodedata.normalize("NFKD", termo)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()
    return " ".join(_TOKEN.findall(sem_acento))


def aceita_nao_ratificado(*, ambiente: str, configurado: bool) -> bool:
    """O portao da ratificacao (decisao da ROOT): `False` em producao e em ambiente desconhecido ou vazio
    (falha fechada); nos ambientes de `AMBIENTES_QUE_PODEM_ACEITAR`, igual a `configurado` (a configuracao
    `CHAVE_ACEITAR_NAO_RATIFICADO`). Nenhuma configuracao faz producao aceitar."""
    return configurado is True and ambiente.strip().lower() in AMBIENTES_QUE_PODEM_ACEITAR


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
    ratificado: bool
    regras: Mapping[str, RegraDoManual]
    capitulo_geral: tuple[SecaoGeral, ...]
    #: termo popular (como publicado) -> ids de regra (`sinonimos-por-termo.json`).
    termos: Mapping[str, tuple[str, ...]]
    #: termo popular (como publicado) -> termos tecnicos (`sinonimos.json`).
    termos_tecnicos: Mapping[str, tuple[str, ...]]
    avaliacoes: tuple[AvaliacaoDoManual, ...]
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
        """Termo popular normalizado (do `sinonimos-por-termo.json`) -> ids de regra, em ordem de id; so' os
        termos com ao menos uma regra."""
        por_termo: dict[str, set[str]] = {}
        for termo_publicado, ids in self.termos.items():
            termo = normalizar(termo_publicado)
            if termo and ids:
                por_termo.setdefault(termo, set()).update(ids)
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


def _campos_exatos(
    bruto: Mapping[str, Any],
    obrigatorios: frozenset[str],
    *,
    prefixo: str,
    opcionais: frozenset[str] = frozenset(),
) -> None:
    """Formato fechado: nenhum campo fora de `obrigatorios | opcionais`, nenhum obrigatorio ausente."""
    if not set(bruto) <= obrigatorios | opcionais:
        raise PacoteK2InvalidoError(f"{prefixo}_com_campo_desconhecido")
    if not set(bruto) >= obrigatorios:
        raise PacoteK2InvalidoError(f"{prefixo}_sem_campo_obrigatorio")


def _texto(valor: object, *, motivo: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise PacoteK2InvalidoError(motivo)
    return valor


def _textos(valor: object, *, motivo: str) -> tuple[str, ...]:
    """Lista de textos nao vazios (a lista pode ser vazia)."""
    if not isinstance(valor, list):
        raise PacoteK2InvalidoError(motivo)
    return tuple(_texto(v, motivo=motivo) for v in valor)


def _json(dados: bytes, *, motivo: str) -> Any:
    try:
        return json.loads(dados.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PacoteK2InvalidoError(motivo) from None


def _yaml(dados: bytes, *, motivo: str) -> Any:
    try:
        return yaml.safe_load(dados.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        raise PacoteK2InvalidoError(motivo) from None


def bytes_canonicos(manifesto: Mapping[str, Any]) -> bytes:
    """A serializacao canonica do manifesto pelo contrato (a mesma do gerador do rco)."""
    return json.dumps(manifesto, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _verificar_manifesto(
    dados: bytes, linha_sha256: bytes, pin: PinDoK2, *, aceitar_nao_ratificado: bool
) -> tuple[str, str, bool, dict[str, str]]:
    """O manifesto -> `(versao, commit_rco, ratificado, {arquivo: digest})`, na ordem do docstring."""
    bruto = _json(dados, motivo="manifesto_ilegivel")
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("manifesto_ilegivel")
    if bytes_canonicos(bruto) != dados:
        raise PacoteK2InvalidoError("manifesto_nao_canonico")
    if bruto.get("formato") != FORMATO:
        raise PacoteK2InvalidoError("formato_diferente")
    versao = bruto.get("versao")
    if not isinstance(versao, str) or not versao.strip() or versao != pin.versao:
        raise PacoteK2InvalidoError("versao_diferente_do_pin")
    digest = _PREFIXO_DO_DIGEST + hashlib.sha256(dados).hexdigest()
    if digest != _PREFIXO_DO_DIGEST + pin.sha256_manifesto.lower():
        raise PacoteK2InvalidoError("sha256_do_manifesto_diferente_do_pin")
    _verificar_linha_sha256(linha_sha256, digest)
    _campos_exatos(bruto, _CAMPOS_DO_MANIFESTO, prefixo="manifesto")
    ratificado = bruto["ratificado"]
    if not isinstance(ratificado, bool):
        raise PacoteK2InvalidoError("ratificado_invalido")
    if not ratificado and aceitar_nao_ratificado is not True:
        raise PacoteK2InvalidoError("pacote_nao_ratificado")
    commit = bruto["commit_rco"]
    if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
        raise PacoteK2InvalidoError("commit_rco_invalido")
    if not _e_instante_utc(bruto["gerado_em"]):
        raise PacoteK2InvalidoError("gerado_em_invalido")
    arquivos = bruto["arquivos"]
    if not isinstance(arquivos, dict) or set(arquivos) != ARQUIVOS_DO_PACOTE:
        raise PacoteK2InvalidoError("lista_de_arquivos_invalida")
    for hash_ in arquivos.values():
        if not isinstance(hash_, str) or not _DIGEST.fullmatch(hash_):
            raise PacoteK2InvalidoError("lista_de_arquivos_invalida")
    return versao, commit, ratificado, dict(arquivos)


def _verificar_linha_sha256(linha: bytes, digest: str) -> None:
    """`manifesto.sha256` = EXATAMENTE `sha256:<hex>` e UMA quebra de linha (o gerador do rco)."""
    try:
        conteudo = linha.decode("ascii")
    except UnicodeDecodeError:
        raise PacoteK2InvalidoError("manifesto_sha256_fora_do_formato") from None
    if not conteudo.endswith("\n") or not _DIGEST.fullmatch(conteudo[:-1]):
        raise PacoteK2InvalidoError("manifesto_sha256_fora_do_formato")
    if conteudo[:-1] != digest:
        raise PacoteK2InvalidoError("manifesto_sha256_diferente")


def _e_instante_utc(valor: object) -> bool:
    """`AAAA-MM-DDTHH:MM:SSZ`, e uma data/hora que existe."""
    if not isinstance(valor, str) or not _INSTANTE_UTC.fullmatch(valor):
        return False
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _verificar_diretorio(diretorio: Path, listados: Mapping[str, str]) -> dict[str, bytes]:
    """Nenhuma entrada fora dos sete; cada arquivo listado presente e com o sha256 do manifesto."""
    for entrada in diretorio.iterdir():
        if entrada.name not in ENTRADAS_DO_DIRETORIO:
            raise PacoteK2InvalidoError("arquivo_nao_listado_no_manifesto")
    conteudos: dict[str, bytes] = {}
    for nome, hash_ in sorted(listados.items()):
        dados = _ler(diretorio / nome, ausente="arquivo_listado_ausente")
        if _PREFIXO_DO_DIGEST + hashlib.sha256(dados).hexdigest() != hash_:
            raise PacoteK2InvalidoError("sha256_de_arquivo_diferente")
        conteudos[nome] = dados
    return conteudos


def _mapa_de_listas(bruto: object, *, motivo: str) -> dict[str, tuple[str, ...]]:
    """`{texto: [texto, ...]}`, chaves e itens nao vazios, sem item repetido numa lista."""
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError(motivo)
    saida: dict[str, tuple[str, ...]] = {}
    for chave, valores in bruto.items():
        itens = _textos(valores, motivo=motivo)
        if len(set(itens)) != len(itens):
            raise PacoteK2InvalidoError(motivo)
        saida[_texto(chave, motivo=motivo)] = itens
    return saida


def _regra(item: object) -> RegraDoManual:
    if not isinstance(item, dict):
        raise PacoteK2InvalidoError("regra_fora_do_formato")
    _campos_exatos(item, _CAMPOS_DA_REGRA, prefixo="regra")
    if item["publico"] != PUBLICO:
        raise PacoteK2InvalidoError("regra_de_outro_publico")
    sinonimos = _textos(item["sinonimos"], motivo="regra_fora_do_formato")
    if list(sinonimos) != sorted(set(sinonimos)):
        raise PacoteK2InvalidoError("regra_fora_do_formato")
    for campo in ("publico_nome", "titulo", "url"):
        _texto(item[campo], motivo="regra_fora_do_formato")
    for campo in ("t_titulo", "t_chaves", "t_texto"):
        if not isinstance(item[campo], str):
            raise PacoteK2InvalidoError("regra_fora_do_formato")
    return RegraDoManual(
        id=_texto(item["id"], motivo="regra_sem_id"),
        url=item["url"],
        titulo=item["titulo"],
        resumo=_texto(item["resumo"], motivo="regra_sem_resumo"),
        texto=_texto(item["texto"], motivo="regra_sem_texto"),
        sinonimos=sinonimos,
        versao=_texto(item["versao"], motivo="regra_fora_do_formato"),
        dominio=_texto(item["dominio"], motivo="regra_fora_do_formato"),
        dominio_nome=_texto(item["dominio_nome"], motivo="regra_fora_do_formato"),
    )


def _indice(dados: bytes, *, versao: str, sinonimos: object) -> dict[str, RegraDoManual]:
    bruto = _json(dados, motivo="indice_json_invalido")
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("indice_fora_do_formato")
    _campos_exatos(bruto, _CAMPOS_DO_INDICE, prefixo="indice")
    if bruto["publico"] != PUBLICO:
        raise PacoteK2InvalidoError("indice_de_outro_publico")
    if bruto["versao_manual"] != versao:
        raise PacoteK2InvalidoError("versao_do_indice_diferente_do_manifesto")
    _texto(bruto["data_versao"], motivo="indice_fora_do_formato")
    if bruto["sinonimos"] != sinonimos:
        raise PacoteK2InvalidoError("sinonimos_do_indice_diferentes")
    documentos = bruto["documentos"]
    if not isinstance(documentos, list):
        raise PacoteK2InvalidoError("indice_fora_do_formato")
    if not documentos:
        raise PacoteK2InvalidoError("indice_sem_regras")
    regras: dict[str, RegraDoManual] = {}
    for item in documentos:
        regra = _regra(item)
        if regra.id in regras:
            raise PacoteK2InvalidoError("id_de_regra_duplicado")
        regras[regra.id] = regra
    return regras


def _verificar_sinonimos(
    termos_tecnicos: Mapping[str, tuple[str, ...]],
    termos: Mapping[str, tuple[str, ...]],
    regras: Mapping[str, RegraDoManual],
) -> None:
    """As chaves dos dois arquivos sao as mesmas; todo id existe; o `sinonimos` de cada documento e' a
    projecao inversa do `sinonimos-por-termo.json`."""
    if set(termos) != set(termos_tecnicos):
        raise PacoteK2InvalidoError("sinonimos_por_termo_com_chaves_diferentes")
    inverso: dict[str, set[str]] = {id_: set() for id_ in regras}
    for termo, ids in termos.items():
        for id_ in ids:
            if id_ not in inverso:
                raise PacoteK2InvalidoError("sinonimos_por_termo_com_regra_desconhecida")
            inverso[id_].add(termo)
    for id_, regra in regras.items():
        if list(regra.sinonimos) != sorted(inverso[id_]):
            raise PacoteK2InvalidoError("sinonimos_da_regra_inconsistentes")


def _capitulo_geral(dados: bytes) -> tuple[SecaoGeral, ...]:
    bruto = _yaml(dados, motivo="capitulo_geral_yaml_invalido")
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("capitulo_geral_fora_do_formato")
    _campos_exatos(bruto, frozenset({"secoes"}), prefixo="capitulo_geral")
    secoes_brutas = bruto["secoes"]
    if not isinstance(secoes_brutas, list) or not secoes_brutas:
        raise PacoteK2InvalidoError("capitulo_geral_fora_do_formato")
    secoes: list[SecaoGeral] = []
    for item in secoes_brutas:
        if not isinstance(item, dict):
            raise PacoteK2InvalidoError("secao_fora_do_formato")
        _campos_exatos(item, _CAMPOS_DA_SECAO, prefixo="secao")
        secoes.append(
            SecaoGeral(
                id=_texto(item["id"], motivo="secao_fora_do_formato"),
                titulo=_texto(item["titulo"], motivo="secao_fora_do_formato"),
                texto=_texto(item["texto"], motivo="secao_sem_texto"),
            )
        )
    if len({s.id for s in secoes}) != len(secoes):
        raise PacoteK2InvalidoError("id_de_secao_duplicado")
    return tuple(secoes)


def _avaliacao(item: object, regras: Mapping[str, RegraDoManual]) -> AvaliacaoDoManual:
    if not isinstance(item, dict):
        raise PacoteK2InvalidoError("avaliacao_fora_do_formato")
    _campos_exatos(item, _CAMPOS_DA_AVALIACAO, prefixo="avaliacao", opcionais=_CAMPOS_OPCIONAIS_DA_AVALIACAO)
    if item["publico"] != PUBLICO:
        raise PacoteK2InvalidoError("avaliacao_de_outro_publico")
    comportamento = item.get("comportamento")
    if "comportamento" in item and not (isinstance(comportamento, str) and comportamento in COMPORTAMENTOS):
        raise PacoteK2InvalidoError("avaliacao_com_comportamento_invalido")
    regra_origem = _textos(item["regra_origem"], motivo="avaliacao_fora_do_formato")
    if any(id_ not in regras for id_ in regra_origem):
        raise PacoteK2InvalidoError("avaliacao_com_regra_desconhecida")
    motivo = "avaliacao_fora_do_formato"
    return AvaliacaoDoManual(
        id=_texto(item["id"], motivo=motivo),
        publico=PUBLICO,
        pergunta=_texto(item["pergunta"], motivo=motivo),
        resposta_esperada=_texto(item["resposta_esperada"], motivo=motivo),
        elementos_obrigatorios=_textos(item["elementos_obrigatorios"], motivo=motivo),
        o_que_seria_invencao=_textos(item["o_que_seria_invencao"], motivo=motivo),
        regra_origem=regra_origem,
        comportamento=comportamento,
    )


def _avaliacoes(dados: bytes, regras: Mapping[str, RegraDoManual]) -> tuple[AvaliacaoDoManual, ...]:
    bruto = _yaml(dados, motivo="avaliacoes_yaml_invalido")
    if not isinstance(bruto, dict):
        raise PacoteK2InvalidoError("avaliacoes_fora_do_formato")
    _campos_exatos(bruto, frozenset({"avaliacoes"}), prefixo="avaliacoes")
    itens = bruto["avaliacoes"]
    if not isinstance(itens, list):
        raise PacoteK2InvalidoError("avaliacoes_fora_do_formato")
    avaliacoes = tuple(_avaliacao(item, regras) for item in itens)
    if len({a.id for a in avaliacoes}) != len(avaliacoes):
        raise PacoteK2InvalidoError("id_de_avaliacao_duplicado")
    return avaliacoes


def _congelar(mapa: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType(dict(sorted(mapa.items())))


def carregar(diretorio: Path, *, pin: PinDoK2, aceitar_nao_ratificado: bool = False) -> ManualK2:
    """Le' e verifica o pacote K2 contra o pin. Qualquer desvio -> `PacoteK2InvalidoError` (falha fechada).

    `aceitar_nao_ratificado` vem de `aceita_nao_ratificado` (producao nunca aceita). `OSError` de leitura
    (permissao, disco) sobe como esta; `carregar_ou_nenhum` trata os dois."""
    if not diretorio.is_dir():
        raise PacoteK2InvalidoError("diretorio_ausente")
    dados_do_manifesto = _ler(diretorio / MANIFESTO, ausente="manifesto_ausente")
    linha_sha256 = _ler(diretorio / MANIFESTO_SHA256, ausente="manifesto_sha256_ausente")
    versao, commit_rco, ratificado, listados = _verificar_manifesto(
        dados_do_manifesto, linha_sha256, pin, aceitar_nao_ratificado=aceitar_nao_ratificado
    )
    conteudos = _verificar_diretorio(diretorio, listados)
    termos_tecnicos_brutos = _json(conteudos[SINONIMOS], motivo="sinonimos_json_invalido")
    termos_tecnicos = _mapa_de_listas(termos_tecnicos_brutos, motivo="sinonimos_fora_do_formato")
    regras = _indice(conteudos[INDICE], versao=versao, sinonimos=termos_tecnicos_brutos)
    termos = _mapa_de_listas(
        _json(conteudos[SINONIMOS_POR_TERMO], motivo="sinonimos_por_termo_json_invalido"),
        motivo="sinonimos_por_termo_fora_do_formato",
    )
    _verificar_sinonimos(termos_tecnicos, termos, regras)
    return ManualK2(
        versao=versao,
        commit_rco=commit_rco,
        ratificado=ratificado,
        regras=MappingProxyType(dict(sorted(regras.items()))),
        capitulo_geral=_capitulo_geral(conteudos[CAPITULO_GERAL]),
        termos=_congelar(termos),
        termos_tecnicos=_congelar(termos_tecnicos),
        avaliacoes=_avaliacoes(conteudos[AVALIACOES], regras),
    )


def carregar_ou_nenhum(
    diretorio: Path | None, *, pin: PinDoK2 | None, aceitar_nao_ratificado: bool = False
) -> ManualK2 | None:
    """`carregar`, ou `None` (a habilidade cai no aviso de canais) com o log `helena_k2_recusado` so' com o
    motivo: sem diretorio, sem pin, pacote invalido ou erro de leitura."""
    if diretorio is None or pin is None:
        logger.warning("helena_k2_recusado", motivo="sem_diretorio" if diretorio is None else "sem_pin")
        return None
    try:
        return carregar(diretorio, pin=pin, aceitar_nao_ratificado=aceitar_nao_ratificado)
    except PacoteK2InvalidoError as exc:
        # So' o token de classe no log (cerca LUC-06/NEW-01): o motivo exato e' o de `carregar`, que o teste
        # e o operador obtem chamando-o direto sobre o mesmo pacote.
        logger.warning("helena_k2_recusado", motivo="pacote_invalido", erro=type(exc).__name__)
    except OSError as exc:
        logger.warning("helena_k2_recusado", motivo="erro_de_leitura", erro=type(exc).__name__)
    return None


# --- O lock do consumidor ----------------------------------------------------------------------------------
def _sub_objeto(valor: object, campos: frozenset[str]) -> dict[str, Any]:
    if not isinstance(valor, dict) or set(valor) != campos:
        raise PacoteK2InvalidoError("lock_fora_do_formato")
    return valor


def pin_do_lock(caminho: Path) -> PinDoK2 | None:
    """O pin lido do lock do consumidor (`config/integrations/rco/k2.lock.json`, publicado por outro time)::

        {"lock_format": {"name": "rco-k2-lock", "version": "1.0.0"}, "estado": "sem_pacote" | "fixado",
         "provenance": {"rco_repo": str, "rco_commit_sha": 40 hex},
         "manifest_pin": {"path": str, "sha256": "sha256:<hex>"}, "versao": str}

    Arquivo ausente ou `estado: sem_pacote` -> `None`; `fixado` -> `PinDoK2` (o hex SEM o prefixo); qualquer
    outra coisa -> `PacoteK2InvalidoError`. PONTO EM ABERTO: em `sem_pacote` so' `lock_format` e `estado`
    sao exigidos (nao se sabe se os demais vem); campo fora dos cinco e' recusado em qualquer estado. A versao
    do formato do lock e' exata (`1.0.0`): versao nova = leitor novo."""
    if not caminho.exists() and not caminho.is_symlink():
        return None
    bruto = _json(_ler(caminho, ausente="lock_ausente"), motivo="lock_ilegivel")
    if not isinstance(bruto, dict) or not set(bruto) <= _CAMPOS_DO_LOCK:
        raise PacoteK2InvalidoError("lock_fora_do_formato")
    if bruto.get("lock_format") != dict(FORMATO_DO_LOCK):
        raise PacoteK2InvalidoError("lock_formato_diferente")
    estado = bruto.get("estado")
    if estado == "sem_pacote":
        return None
    if estado != "fixado":
        raise PacoteK2InvalidoError("lock_estado_invalido")
    if set(bruto) != _CAMPOS_DO_LOCK:
        raise PacoteK2InvalidoError("lock_fora_do_formato")
    proveniencia = _sub_objeto(bruto["provenance"], frozenset({"rco_repo", "rco_commit_sha"}))
    _texto(proveniencia["rco_repo"], motivo="lock_fora_do_formato")
    commit = proveniencia["rco_commit_sha"]
    if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
        raise PacoteK2InvalidoError("lock_fora_do_formato")
    manifest_pin = _sub_objeto(bruto["manifest_pin"], frozenset({"path", "sha256"}))
    _texto(manifest_pin["path"], motivo="lock_fora_do_formato")
    digest = manifest_pin["sha256"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise PacoteK2InvalidoError("lock_fora_do_formato")
    versao = _texto(bruto["versao"], motivo="lock_fora_do_formato")
    return PinDoK2(versao=versao, sha256_manifesto=digest.removeprefix(_PREFIXO_DO_DIGEST))


__all__ = [
    "AMBIENTES_QUE_PODEM_ACEITAR",
    "ARQUIVOS_DO_PACOTE",
    "AVALIACOES",
    "CAPITULO_GERAL",
    "CHAVE_ACEITAR_NAO_RATIFICADO",
    "COMPORTAMENTOS",
    "ENTRADAS_DO_DIRETORIO",
    "FORMATO",
    "FORMATO_DO_LOCK",
    "INDICE",
    "MANIFESTO",
    "MANIFESTO_SHA256",
    "PUBLICO",
    "SINONIMOS",
    "SINONIMOS_POR_TERMO",
    "TAMANHO_MAXIMO",
    "VARIAVEL_DO_AMBIENTE",
    "AvaliacaoDoManual",
    "ManualK2",
    "PacoteK2InvalidoError",
    "PinDoK2",
    "RegraDoManual",
    "SecaoGeral",
    "aceita_nao_ratificado",
    "bytes_canonicos",
    "carregar",
    "carregar_ou_nenhum",
    "normalizar",
    "pin_do_lock",
]
