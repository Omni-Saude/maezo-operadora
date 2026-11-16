"""Leitor do manifesto do pacote K1 do rco (ADR-0075 secao (b); contrato-k1.md §3-§6, itens 1-6 do §11).

O QUE ISTO E. O rco publica o K1 como `build/pacote/k1/` (contrato `docs/programa/contrato-k1.md` no
repositorio `regras-contratos-operadora`): `manifesto.json` em bytes canonicos, `vocabulario.json`,
`entradas.json`, `k6-filas.json`, `dmn/<arquivo>.dmn` e `exemplos/<id>.json`. O Maezo vendoriza:

- o manifesto e os tres anexos em `config/integrations/rco/k1/` (pasta plana, ao lado do pin);
- cada DMN em `spec/processes/dmn/rco/<arquivo>.dmn` (`caminho_consumidor`, plano — §11 item 4);
- NUNCA os exemplos: sao material de teste do pacote, fora do lock (§3, §11 item 5).

Este modulo e o UNICO leitor do manifesto e das pastas do pacote. O gate de CI
(`scripts/ci/verify_rco_rules_pin.py`, que o carrega PELO CAMINHO do arquivo, sem passar por
`maezo/platform/__init__.py`) e o carregador de runtime (`maezo.tools.workers.rco_pin`) o usam, para
que nao haja dois formatos nem duas varreduras. So biblioteca padrao — nenhum import de `maezo` — e
sem rede.

FAIL-CLOSED (contrato §4 e §5):
- digest em toda parte no formato `sha256:<64 hex minusculos>`; hex cru e recusado (§1 — o hex cru
  dispara o detector de segredos; mesma notacao do K2, HEL-3a);
- `formato_manifesto` com MAJOR 1 ou 2 e MINOR ate o que este leitor conhece (2.1: `provas[]`, obrigatorio
  a partir de 2.1.0 e campo desconhecido, logo recusado, antes); outro e recusado.
  MAJOR 2 (contrato-k1.md 2.0.0, PROG-RCO-8, estavel na branch `prog/rco/ativacao-k1` do rco no
  commit 75015304, ainda NAO em `main` do rco): cada regra tem exatamente `idRegra`,
  `versaoRegra`, `situacao`, `validacao_status`, `classe_adverso`, `ativacao`, `exige_aditivo` e
  `sha256_yaml` (sem `ratificavel_automatico`); `ativacao` no conjunto fechado, igual a
  `vocabulario.json` `ativacao.valores`; `exige_aditivo` booleano; o bloco `ativacao.parametros` de
  `vocabulario.json` (numeros de D1/D2) e lido tipado e exposto — a politica de execucao NAO e daqui;
- `origem.sujo` presente (pacote de workspace sujo) e recusado;
- campos EXATOS no topo, em cada objeto aninhado, em cada decisao e em cada regra: campo a mais ou a
  menos e erro (§4 item 7; um MINOR novo de formato exige atualizar este leitor antes de pinar);
- bytes canonicos (`sort_keys`, `ensure_ascii=False`, separadores sem espaco), chave JSON repetida
  recusada;
- id de decisao com o prefixo `rco_`, igual ao `<decision>` do arquivo; `idRegra` no padrao;
- anexos (`vocabulario.json`, `entradas.json`, `k6-filas.json`) com o sha256 do manifesto;
- colunas de saida da tabela: as obrigatorias de `vocabulario.json` presentes, e qualquer coluna fora
  do `saidas[]` daquela decisao recusada (§6 "Saidas extras", §11 item 6);
- o conjunto de `idRegra` literais nao vazios que a tabela emite e exatamente o de `regras[]` (§3: uma
  entrada por `idRegra` emitido); linha adversa (`combinacao.adversas` de `vocabulario.json`) tem
  preenchidos os campos de `combinacao.adversa_exige_preenchidos` (§6), `idRegra` incluido;
- `provas_ausentes_no_pin`: pacote abaixo de 2.1.0 (sem `provas[]`) ou com `provas[]` vazia e recusado NO
  PIN (o leitor ainda o le para diagnostico; decisao da ROOT, 16/11/2026);
- `ativacao_nao_suportada`: pacote MAJOR 2 com regra fora de `ativacao: plena` sem aditivo e
  recusado ate a Onda 2 item 5 (ativacao por classe de risco) ligar o significado;
- `verificar_pastas`: `spec/processes/dmn/rco/` e `config/integrations/rco/k1/` so com o que o pin e
  o manifesto declaram (sem subpasta, link simbolico ou arquivo solto).

Este modulo NAO decide regra de negocio: so le e confere bytes e forma. O vocabulario fechado das
saidas e conferido pelo lint `platform/validation/vocabulario_saida.py`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final
from xml.etree import ElementTree as ET

#: Pasta (relativa a raiz do repo) do manifesto K1 e dos anexos vendorizados. Plana.
K1_DIR: Final[str] = "config/integrations/rco/k1"
#: Nome do manifesto dentro de `K1_DIR`; `manifest_pin.path` do lock e exatamente `K1_MANIFESTO_PATH`.
K1_MANIFESTO_NOME: Final[str] = "manifesto.json"
K1_MANIFESTO_PATH: Final[str] = f"{K1_DIR}/{K1_MANIFESTO_NOME}"
#: Pasta do consumidor para os DMN do K1 (= `lock_format.rules_dir`).
RULES_DIR: Final[str] = "spec/processes/dmn/rco"
PREFIXO_DECISAO: Final[str] = "rco_"

#: MAJOR do `formato_manifesto` que este leitor suporta -> o maior MINOR que ele conhece (§5).
#: MAJOR 2 (contrato-k1.md 2.0.0, PROG-RCO-8): por regra, sai `ratificavel_automatico` e entram
#: `ativacao` (conjunto fechado) e `exige_aditivo` (booleano); `vocabulario.json` ganha o bloco
#: `ativacao` (`valores` + `parametros`). Campo a mais ou a menos continua recusado.
FORMATOS_SUPORTADOS: Final[Mapping[int, int]] = MappingProxyType({1: 0, 2: 1})
#: Primeiro `(MAJOR, MINOR)` com o campo obrigatorio `provas[]` (contrato-k1.md §3, PROG-RCO-8, 2.1.0).
#: Abaixo disso o campo e desconhecido e recusado (leitor estrito).
FORMATO_COM_PROVAS: Final[tuple[int, int]] = (2, 1)
PROVAS_DIR: Final[str] = "provas"
#: Valores de `regras[].ativacao` (MAJOR 2, contrato §7). Valor fora do conjunto reprova (fail-closed).
ATIVACOES: Final[frozenset[str]] = frozenset({"plena", "conferida", "em_correcao"})
#: Chaves EXATAS de `vocabulario.json` `ativacao.parametros` (MAJOR 2). Os numeros vem do pacote,
#: nunca do codigo (provisorios no rco: `validacao/calibracao/parametros-provisorios/comum.csv`).
PARAMETROS_ATIVACAO: Final[tuple[str, ...]] = (
    "revisao_inicial_decisoes",
    "revisao_inicial_prazo_dias",
    "amostra_posterior_pct",
    "limiar_reversao_pct",
    "limiar_reversao_min_decisoes",
    "conferencia_negativa_admin_decisoes",
)

#: Papeis dos anexos do manifesto -> (secao do manifesto, campo com o sha256 do ARQUIVO).
ANEXOS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "vocabulario": ("vocabulario", "sha256"),
        "entradas": ("dicionario_entradas", "sha256_arquivo"),
        "k6": ("k6", "sha256"),
    }
)

# Conjuntos exatos de campos (contrato-k1.md §3). `valores_emitidos` so existe em algumas saidas.
CAMPOS_TOPO: Final[frozenset[str]] = frozenset(
    {
        "formato_manifesto",
        "versao_pacote",
        "origem",
        "prefixo_decisao",
        "dicionario_entradas",
        "vocabulario",
        "k6",
        "decisoes",
        "exemplos_sinteticos",
    }
)
#: Campo acrescentado no topo a partir do `formato_manifesto` 2.1.0.
CAMPO_TOPO_PROVAS: Final[str] = "provas"
CAMPOS_PROVA: Final[frozenset[str]] = frozenset({"id", "descricao", "arquivos", "esperado"})
CAMPOS_ARQUIVO_PROVA: Final[frozenset[str]] = frozenset({"caminho", "sha256"})
CAMPOS_ORIGEM: Final[frozenset[str]] = frozenset({"repo", "commit_sha"})
CAMPOS_DICIONARIO: Final[frozenset[str]] = frozenset({"path", "sha256", "sha256_arquivo", "versao"})
CAMPOS_VOCABULARIO: Final[frozenset[str]] = frozenset({"path", "sha256"})
CAMPOS_K6: Final[frozenset[str]] = frozenset({"path", "sha256", "versao"})
CAMPOS_DECISAO: Final[frozenset[str]] = frozenset(
    {
        "arquivo",
        "caminho_consumidor",
        "dominio",
        "id_original",
        "id",
        "sha256",
        "hit_policy",
        "history_ttl",
        "entradas",
        "saidas",
        "regras",
    }
)
CAMPOS_ENTRADA: Final[frozenset[str]] = frozenset(
    {"nome", "typeRef", "nome_snake", "nome_maezo", "origem", "dominio_valores"}
)
CAMPOS_SAIDA: Final[frozenset[str]] = frozenset({"nome", "typeRef", "nome_snake"})
CAMPOS_SAIDA_OPCIONAIS: Final[frozenset[str]] = frozenset({"valores_emitidos"})
_CAMPOS_REGRA_COMUNS: Final[frozenset[str]] = frozenset(
    {"idRegra", "versaoRegra", "situacao", "validacao_status", "classe_adverso", "sha256_yaml"}
)
#: Campos exatos de `regras[]` por MAJOR do formato. Nenhuma logica depende de
#: `ratificavel_automatico` (MAJOR 1): ele so e conferido na forma.
CAMPOS_REGRA_POR_MAJOR: Final[Mapping[int, frozenset[str]]] = MappingProxyType(
    {
        1: _CAMPOS_REGRA_COMUNS | {"ratificavel_automatico"},
        2: _CAMPOS_REGRA_COMUNS | {"ativacao", "exige_aditivo"},
    }
)
CAMPOS_EXEMPLO: Final[frozenset[str]] = frozenset({"path", "sha256", "decisao", "classe_adverso", "caso"})

_DIGEST: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_SEMVER: Final[re.Pattern[str]] = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_ID_REGRA: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NOME_PLANO: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")
_DMN_NS: Final[str] = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"
_LITERAL: Final[re.Pattern[str]] = re.compile(r'^"((?:[^"\\]|\\.)*)"$', re.DOTALL)
#: Saida da tabela que identifica a regra casada (contrato §3/§6).
SAIDA_ID_REGRA: Final[str] = "idRegra"
README_NOME: Final[str] = "README.md"


class ManifestoK1InvalidoError(ValueError):
    """O manifesto K1 (ou o pacote que ele descreve) nao pode ser aceito. Carrega todos os motivos."""

    def __init__(self, problemas: list[str]) -> None:
        self.problemas = tuple(problemas)
        super().__init__("; ".join(problemas))


def is_digest(valor: object) -> bool:
    """`True` so para `sha256:<64 hex minusculos>` (a notacao do pacote e do lock)."""
    return isinstance(valor, str) and _DIGEST.fullmatch(valor) is not None


def sha256_prefixado(raw: bytes) -> str:
    """Digest dos bytes na notacao do pacote: `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def bytes_canonicos(valor: Any) -> bytes:
    """Bytes canonicos do contrato §3 (sem quebra de linha final)."""
    return json.dumps(valor, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RegraK1:
    """Uma regra emitida por uma decisao do K1: o que a auditoria grava (§3)."""

    id_regra: str
    versao_regra: str
    sha256_yaml: str
    ativacao: str | None = None  # so MAJOR 2; None no MAJOR 1 (nunca inventado)
    exige_aditivo: bool | None = None  # so MAJOR 2


@dataclass(frozen=True, slots=True)
class DecisaoK1:
    """Uma decisao do K1, como o manifesto a descreve."""

    id: str
    arquivo: str  # `dmn/<arquivo>.dmn`, relativo a k1/ no pacote
    caminho_consumidor: str  # `spec/processes/dmn/rco/<arquivo>.dmn`
    sha256: str  # `sha256:<hex>` do arquivo do pacote; e o `shaDmn` da auditoria
    saidas: tuple[str, ...]  # nomes das colunas de saida declaradas (`saidas[].nome`)
    regras: Mapping[str, RegraK1]  # por `idRegra`
    dominio: str = ""  # `dominio` do manifesto (pasta do rco); o montador de entradas escolhe por ele
    entradas: tuple[str, ...] = ()  # `entradas[].nome` (nome DMN), na ordem do manifesto
    #: `(nome, origem, nome_maezo)` de cada `entradas[]`: o leitor do dicionario confere contra ele.
    entradas_declaradas: tuple[tuple[str, object, object], ...] = ()


@dataclass(frozen=True, slots=True)
class AnexoK1:
    """Um anexo pinado pelo manifesto (`vocabulario.json`, `entradas.json`, `k6-filas.json`)."""

    papel: str
    nome: str  # nome plano do arquivo dentro de k1/
    sha256: str


@dataclass(frozen=True, slots=True)
class ArquivoProvaK1:
    """Um arquivo de uma prova: caminho relativo a `k1/` (sob `provas/<id>/`) e o digest do manifesto."""

    caminho: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProvaK1:
    """Uma prova de comportamento do motor (contrato §3, `formato_manifesto` >= 2.1.0). Fora do lock."""

    id: str
    descricao: str
    arquivos: tuple[ArquivoProvaK1, ...]
    esperado: str  # `provas/<id>/validacao/casos`


@dataclass(frozen=True, slots=True)
class ManifestoK1:
    """O manifesto K1 ja validado na forma."""

    formato_manifesto: str
    versao_pacote: str
    repo: str
    commit_sha: str
    decisoes: Mapping[str, DecisaoK1]  # por id (prefixado)
    anexos: Mapping[str, AnexoK1]  # por papel
    exemplos: tuple[str, ...]  # caminhos dos exemplos no pacote: so listados, nunca vendorizados
    #: `dicionario_entradas.versao` e `.sha256` (hash do CONTEUDO = `sha256_conteudo` do arquivo).
    dicionario_entradas_versao: str = ""
    dicionario_entradas_sha256: str = ""
    #: `provas[]` (so `formato_manifesto` >= 2.1.0; vazio antes). Nunca entram no lock.
    provas: tuple[ProvaK1, ...] = ()

    @property
    def major(self) -> int:
        return int(self.formato_manifesto.split(".", 1)[0])

    def decisao_por_caminho(self) -> dict[str, DecisaoK1]:
        return {d.caminho_consumidor: d for d in self.decisoes.values()}


@dataclass(frozen=True, slots=True)
class ParametrosAtivacao:
    """`vocabulario.json` `ativacao.parametros` (MAJOR 2, contrato §7): os numeros de D1/D2.

    So leitura tipada; quem aplica (contadores, amostra, limiar) e unidade posterior. Os valores sao
    provisorios no rco e mudam com o pacote, nunca no codigo.
    """

    revisao_inicial_decisoes: int
    revisao_inicial_prazo_dias: int
    amostra_posterior_pct: int | float
    limiar_reversao_pct: int | float
    limiar_reversao_min_decisoes: int
    conferencia_negativa_admin_decisoes: int


@dataclass(frozen=True, slots=True)
class VocabularioPacoteK1:
    """O que o leitor tira de `vocabulario.json` (pinado pelo hash do manifesto)."""

    saidas_obrigatorias: tuple[str, ...]
    ativacoes: frozenset[str] | None  # `ativacao.valores`; None no MAJOR 1
    parametros_ativacao: ParametrosAtivacao | None  # None no MAJOR 1
    #: `combinacao.adversas` e `combinacao.adversa_exige_preenchidos` (contrato §6): decisao adversa
    #: exige esses campos preenchidos na linha da tabela.
    adversas: frozenset[str] = frozenset()
    adversa_exige_preenchidos: tuple[str, ...] = ()


class _ChaveDuplicadaError(ValueError):
    pass


def _sem_chave_duplicada(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for chave, valor in pares:
        if chave in out:
            raise _ChaveDuplicadaError(chave)
        out[chave] = valor
    return out


def _sem_constante(nome: str) -> Any:
    raise ValueError(f"constante JSON nao padrao {nome!r} (NaN/Infinity) recusada")


def json_estrito(raw: bytes) -> Any:
    """`json.loads` fail-closed: UTF-8, sem chave repetida e sem NaN/Infinity. Levanta `ValueError`."""
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_sem_chave_duplicada, parse_constant=_sem_constante
    )


def _carregar_json(raw: bytes, rotulo: str, problemas: list[str]) -> Any:
    try:
        return json_estrito(raw)
    except _ChaveDuplicadaError as exc:
        problemas.append(f"{rotulo}: chave JSON repetida {exc.args[0]!r}")
    except ValueError as exc:  # inclui UnicodeDecodeError e NaN/Infinity
        problemas.append(f"{rotulo}: JSON ilegivel ({type(exc).__name__})")
    except RecursionError:  # `[` aninhado demais
        problemas.append(f"{rotulo}: JSON ilegivel (RecursionError)")
    return None


def _parse_xml_sem_dtd(raw: bytes) -> ET.Element:
    """Mesma recusa de `maezo.platform.xml_safe.parse_xml_without_dtd` (F-152/F-153), so stdlib.

    Copiada aqui para que o gate carregue este modulo sem importar o pacote `maezo.platform`.
    """
    if b"\x00" in raw or raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise ValueError("XML tem de ser UTF-8 (UTF-16/32 ou NUL recusados)")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("XML com DOCTYPE/ENTITY recusado")
    return ET.fromstring(raw)


def _objeto(
    no: object,
    campos: frozenset[str],
    rotulo: str,
    problemas: list[str],
    *,
    opcionais: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    if not isinstance(no, dict):
        problemas.append(f"{rotulo}: tem de ser objeto")
        return None
    chaves = set(no)
    faltam = sorted(campos - chaves)
    sobram = sorted(chaves - campos - opcionais)
    if faltam or sobram:
        problemas.append(f"{rotulo}: campos diferentes do contrato (faltam {faltam}, desconhecidos {sobram})")
        return None
    return no


def _texto(
    no: object, rotulo: str, problemas: list[str], padrao: re.Pattern[str] | None = None
) -> str | None:
    if not isinstance(no, str) or not no or (padrao is not None and padrao.fullmatch(no) is None):
        exigido = f" no padrao {padrao.pattern}" if padrao is not None else ""
        problemas.append(f"{rotulo}: tem de ser texto nao vazio{exigido}")
        return None
    return no


def _digest(no: object, rotulo: str, problemas: list[str]) -> str | None:
    if not is_digest(no):
        problemas.append(f"{rotulo}: digest tem de ser sha256:<64 hex minusculos>")
        return None
    return str(no)


def _nome_plano(no: object, rotulo: str, problemas: list[str]) -> str | None:
    nome = _texto(no, rotulo, problemas)
    if nome is None:
        return None
    if _NOME_PLANO.fullmatch(nome) is None or nome in (".", "..") or nome == K1_MANIFESTO_NOME:
        problemas.append(f"{rotulo}: {nome!r} tem de ser nome de arquivo plano (sem pasta)")
        return None
    return nome


def _ler_formato(no: object, problemas: list[str]) -> tuple[str, int] | None:
    formato = _texto(no, "formato_manifesto", problemas, _SEMVER)
    if formato is None:
        return None
    major, minor, _ = (int(p) for p in formato.split("."))
    if major not in FORMATOS_SUPORTADOS or minor > FORMATOS_SUPORTADOS[major]:
        suportados = ", ".join(f"{m}.0-{m}.{n}.x" for m, n in sorted(FORMATOS_SUPORTADOS.items()))
        problemas.append(
            f"formato_manifesto {formato} nao suportado: este leitor le {suportados} "
            "(atualize o leitor antes de pinar; contrato §5)"
        )
        return None
    return formato, major


def _ler_origem(no: object, problemas: list[str]) -> tuple[str, str] | None:
    if isinstance(no, dict) and "sujo" in no:
        problemas.append("origem.sujo presente: pacote gerado de workspace sujo e recusado (contrato §3/§4)")
        return None
    origem = _objeto(no, CAMPOS_ORIGEM, "origem", problemas)
    if origem is None:
        return None
    repo = _texto(origem["repo"], "origem.repo", problemas)
    commit = _texto(origem["commit_sha"], "origem.commit_sha", problemas, _COMMIT)
    if repo is None or commit is None:
        return None
    return repo, commit


def _ler_anexos(manifesto: Mapping[str, Any], problemas: list[str]) -> dict[str, AnexoK1]:
    formas = {"vocabulario": CAMPOS_VOCABULARIO, "dicionario_entradas": CAMPOS_DICIONARIO, "k6": CAMPOS_K6}
    anexos: dict[str, AnexoK1] = {}
    for papel, (secao, campo_sha) in ANEXOS.items():
        obj = _objeto(manifesto[secao], formas[secao], secao, problemas)
        if obj is None:
            continue
        nome = _nome_plano(obj["path"], f"{secao}.path", problemas)
        sha = _digest(obj[campo_sha], f"{secao}.{campo_sha}", problemas)
        if campo_sha != "sha256":  # dicionario_entradas: `sha256` e o hash do CONTEUDO (k1-entradas.md)
            _digest(obj["sha256"], f"{secao}.sha256", problemas)
        if "versao" in obj:
            _texto(obj["versao"], f"{secao}.versao", problemas, _SEMVER)
        if nome is not None and sha is not None:
            anexos[papel] = AnexoK1(papel, nome, sha)
    nomes = [a.nome for a in anexos.values()]
    if len(set(nomes)) != len(nomes):
        problemas.append(f"anexos com o mesmo nome de arquivo: {sorted(nomes)}")
    return anexos


def _ler_saidas(no: object, rotulo: str, problemas: list[str]) -> tuple[str, ...] | None:
    if not isinstance(no, list) or not no:
        problemas.append(f"{rotulo}: tem de ser lista nao vazia")
        return None
    nomes: list[str] = []
    for i, item in enumerate(no):
        saida = _objeto(item, CAMPOS_SAIDA, f"{rotulo}[{i}]", problemas, opcionais=CAMPOS_SAIDA_OPCIONAIS)
        if saida is None:
            return None
        nome = _texto(saida["nome"], f"{rotulo}[{i}].nome", problemas)
        if nome is None:
            return None
        nomes.append(nome)
    if len(set(nomes)) != len(nomes):
        problemas.append(f"{rotulo}: saida declarada mais de uma vez")
        return None
    return tuple(nomes)


def _ler_regras(no: object, rotulo: str, major: int, problemas: list[str]) -> dict[str, RegraK1] | None:
    if not isinstance(no, list):
        problemas.append(f"{rotulo}: tem de ser lista")
        return None
    regras: dict[str, RegraK1] = {}
    for i, item in enumerate(no):
        onde = f"{rotulo}[{i}]"
        regra = _objeto(item, CAMPOS_REGRA_POR_MAJOR[major], onde, problemas)
        if regra is None:
            return None
        id_regra = _texto(regra["idRegra"], f"{onde}.idRegra", problemas, _ID_REGRA)
        versao = _texto(regra["versaoRegra"], f"{onde}.versaoRegra", problemas, _SEMVER)
        sha_yaml = _digest(regra["sha256_yaml"], f"{onde}.sha256_yaml", problemas)
        ativacao: str | None = None
        exige_aditivo: bool | None = None
        if major == 1 and not isinstance(regra["ratificavel_automatico"], bool):
            problemas.append(f"{onde}.ratificavel_automatico: tem de ser booleano")
        if major == 2:
            if not isinstance(regra["ativacao"], str) or regra["ativacao"] not in ATIVACOES:
                problemas.append(f"{onde}.ativacao: tem de ser um de {sorted(ATIVACOES)}")
                return None
            if not isinstance(regra["exige_aditivo"], bool):
                problemas.append(f"{onde}.exige_aditivo: tem de ser booleano")
                return None
            ativacao, exige_aditivo = str(regra["ativacao"]), bool(regra["exige_aditivo"])
        if id_regra is None or versao is None or sha_yaml is None:
            return None
        if id_regra in regras:
            problemas.append(f"{onde}: idRegra {id_regra!r} repetido na decisao")
            return None
        regras[id_regra] = RegraK1(id_regra, versao, sha_yaml, ativacao, exige_aditivo)
    return regras


def _ler_decisao(no: object, i: int, major: int, problemas: list[str]) -> DecisaoK1 | None:
    onde = f"decisoes[{i}]"
    obj = _objeto(no, CAMPOS_DECISAO, onde, problemas)
    if obj is None:
        return None
    did = _texto(obj["id"], f"{onde}.id", problemas)
    original = _texto(obj["id_original"], f"{onde}.id_original", problemas)
    arquivo = _texto(obj["arquivo"], f"{onde}.arquivo", problemas)
    caminho = _texto(obj["caminho_consumidor"], f"{onde}.caminho_consumidor", problemas)
    sha = _digest(obj["sha256"], f"{onde}.sha256", problemas)
    dominio = _texto(obj["dominio"], f"{onde}.dominio", problemas, _NOME_PLANO)
    entradas: list[str] = []
    declaradas: list[tuple[str, object, object]] = []
    if not isinstance(obj["entradas"], list):
        problemas.append(f"{onde}.entradas: tem de ser lista")
    else:
        for j, entrada in enumerate(obj["entradas"]):
            lida = _objeto(entrada, CAMPOS_ENTRADA, f"{onde}.entradas[{j}]", problemas)
            nome_entrada = _texto(lida["nome"], f"{onde}.entradas[{j}].nome", problemas) if lida else None
            if nome_entrada is not None:
                if nome_entrada in entradas:
                    problemas.append(f"{onde}.entradas[{j}]: nome {nome_entrada!r} repetido")
                entradas.append(nome_entrada)
                if lida is not None:
                    declaradas.append((nome_entrada, lida["origem"], lida["nome_maezo"]))
    saidas = _ler_saidas(obj["saidas"], f"{onde}.saidas", problemas)
    regras = _ler_regras(obj["regras"], f"{onde}.regras", major, problemas)
    if (
        did is None
        or original is None
        or arquivo is None
        or caminho is None
        or sha is None
        or dominio is None
        or saidas is None
        or regras is None
    ):
        return None
    ok = True
    if not did.startswith(PREFIXO_DECISAO) or did != PREFIXO_DECISAO + original:
        problemas.append(f"{onde}: id {did!r} tem de ser {PREFIXO_DECISAO!r} + id_original")
        ok = False
    rel_arquivo = PurePosixPath(arquivo)
    nome = rel_arquivo.name
    if (
        str(rel_arquivo) != arquivo
        or rel_arquivo.parent != PurePosixPath("dmn")
        or rel_arquivo.suffix != ".dmn"
        or _NOME_PLANO.fullmatch(nome) is None
    ):
        problemas.append(f"{onde}.arquivo: {arquivo!r} tem de ser dmn/<arquivo>.dmn, sem subpasta")
        ok = False
    elif caminho != f"{RULES_DIR}/{nome}":
        problemas.append(f"{onde}.caminho_consumidor: {caminho!r} tem de ser {RULES_DIR}/{nome} (plano)")
        ok = False
    if not ok:
        return None
    return DecisaoK1(
        did,
        arquivo,
        caminho,
        sha,
        saidas,
        MappingProxyType(regras),
        dominio=dominio,
        entradas=tuple(entradas),
        entradas_declaradas=tuple(declaradas),
    )


def ler_manifesto(raw: bytes) -> ManifestoK1:
    """Le e valida a FORMA do manifesto K1. Levanta `ManifestoK1InvalidoError` com todos os motivos."""
    problemas: list[str] = []
    dados = _carregar_json(raw, "manifesto", problemas)
    if dados is None:
        raise ManifestoK1InvalidoError(problemas)
    try:
        canonicos = bytes_canonicos(dados) == raw
    except UnicodeEncodeError:  # surrogate solitario (\ud800): nao ha bytes canonicos UTF-8
        canonicos = False
    if not canonicos:
        problemas.append("manifesto: bytes nao sao os canonicos (contrato §3); nao reserialize o arquivo")
    if isinstance(dados, dict) and isinstance(dados.get("origem"), dict) and "sujo" in dados["origem"]:
        _ler_origem(dados["origem"], problemas)  # registra a recusa antes da checagem de forma
        raise ManifestoK1InvalidoError(problemas)
    # Sem MAJOR/MINOR conhecido nao ha forma de topo para conferir: so a recusa do formato.
    if (
        isinstance(dados, dict)
        and "formato_manifesto" in dados
        and _ler_formato(dados["formato_manifesto"], problemas) is None
    ):
        raise ManifestoK1InvalidoError(problemas)
    com_provas = _formato_tem_provas(dados)
    campos_topo = CAMPOS_TOPO | {CAMPO_TOPO_PROVAS} if com_provas else CAMPOS_TOPO
    topo = _objeto(dados, campos_topo, "manifesto", problemas)
    if topo is None:
        raise ManifestoK1InvalidoError(problemas)

    lido_formato = _ler_formato(topo["formato_manifesto"], problemas)
    if lido_formato is None:  # sem MAJOR conhecido nao ha forma de regra para conferir
        raise ManifestoK1InvalidoError(problemas)
    formato, major = lido_formato
    versao_pacote = _texto(topo["versao_pacote"], "versao_pacote", problemas, _SEMVER)
    origem = _ler_origem(topo["origem"], problemas)
    if topo["prefixo_decisao"] != PREFIXO_DECISAO:
        problemas.append(f"prefixo_decisao tem de ser {PREFIXO_DECISAO!r}")
    anexos = _ler_anexos(topo, problemas)

    decisoes: dict[str, DecisaoK1] = {}
    nomes: set[str] = set()
    if not isinstance(topo["decisoes"], list) or not topo["decisoes"]:
        problemas.append("decisoes: tem de ser lista nao vazia")
    else:
        for i, item in enumerate(topo["decisoes"]):
            decisao = _ler_decisao(item, i, major, problemas)
            if decisao is None:
                continue
            nome = PurePosixPath(decisao.caminho_consumidor).name
            if decisao.id in decisoes:
                problemas.append(f"decisoes[{i}]: id {decisao.id!r} repetido")
            elif nome in nomes:
                problemas.append(f"decisoes[{i}]: arquivo {nome!r} repetido (pasta plana)")
            else:
                decisoes[decisao.id] = decisao
                nomes.add(nome)

    exemplos: list[str] = []
    if not isinstance(topo["exemplos_sinteticos"], list):
        problemas.append("exemplos_sinteticos: tem de ser lista")
    else:
        for i, item in enumerate(topo["exemplos_sinteticos"]):
            exemplo = _objeto(item, CAMPOS_EXEMPLO, f"exemplos_sinteticos[{i}]", problemas)
            if exemplo is not None:
                _digest(exemplo["sha256"], f"exemplos_sinteticos[{i}].sha256", problemas)
                caminho = _texto(exemplo["path"], f"exemplos_sinteticos[{i}].path", problemas)
                if caminho is not None:
                    exemplos.append(caminho)

    provas = _ler_provas(topo[CAMPO_TOPO_PROVAS], problemas) if com_provas else ()

    if problemas or versao_pacote is None or origem is None:
        raise ManifestoK1InvalidoError(problemas or ["manifesto invalido"])
    return ManifestoK1(
        formato_manifesto=formato,
        versao_pacote=versao_pacote,
        repo=origem[0],
        commit_sha=origem[1],
        decisoes=MappingProxyType(decisoes),
        anexos=MappingProxyType(anexos),
        exemplos=tuple(exemplos),
        dicionario_entradas_versao=str(topo["dicionario_entradas"].get("versao", "")),
        dicionario_entradas_sha256=str(topo["dicionario_entradas"].get("sha256", "")),
        provas=provas,
    )


def _formato_tem_provas(dados: object) -> bool:
    """`True` se `formato_manifesto` e do MAJOR de `FORMATO_COM_PROVAS` com MINOR >= o dele.

    A forma do topo depende disso (`provas` obrigatorio daqui em diante, desconhecido antes).

    Formato ilegivel devolve `False`: `_ler_formato` registra a recusa depois.
    """
    if not isinstance(dados, dict):
        return False
    formato = dados.get("formato_manifesto")
    if not isinstance(formato, str) or _SEMVER.fullmatch(formato) is None:
        return False
    major, minor, _ = (int(p) for p in formato.split("."))
    return major == FORMATO_COM_PROVAS[0] and minor >= FORMATO_COM_PROVAS[1]


def _caminho_de_prova(no: object, rotulo: str, id_prova: str | None, problemas: list[str]) -> str | None:
    """Caminho relativo a `k1/`, POSIX, sem `..`, absoluto ou barra dupla, sob `provas/<id>/`."""
    caminho = _texto(no, rotulo, problemas)
    if caminho is None:
        return None
    partes = caminho.split("/")
    if (
        caminho.startswith("/")
        or "\\" in caminho
        or "\x00" in caminho
        or any(p in ("", ".", "..") for p in partes)
    ):
        problemas.append(f"{rotulo}: caminho inseguro {caminho!r} (absoluto, '..', '.', '\\' ou vazio)")
        return None
    if id_prova is not None and (len(partes) < 3 or partes[:2] != [PROVAS_DIR, id_prova]):
        problemas.append(f"{rotulo}: {caminho!r} tem de ficar sob {PROVAS_DIR}/{id_prova}/")
        return None
    return caminho


def _ler_provas(no: object, problemas: list[str]) -> tuple[ProvaK1, ...]:
    if not isinstance(no, list) or not no:
        problemas.append("provas: tem de ser lista nao vazia")
        return ()
    provas: list[ProvaK1] = []
    ids: set[str] = set()
    caminhos: set[str] = set()
    minusculos: dict[str, str] = {}  # `casefold` -> caminho: sistemas de arquivos que ignoram caixa colidem
    for i, item in enumerate(no):
        rotulo = f"provas[{i}]"
        obj = _objeto(item, CAMPOS_PROVA, rotulo, problemas)
        if obj is None:
            continue
        id_prova = _texto(obj["id"], f"{rotulo}.id", problemas, _NOME_PLANO)
        if id_prova in (".", ".."):
            problemas.append(f"{rotulo}.id: {id_prova!r} invalido")
            id_prova = None
        descricao = _texto(obj["descricao"], f"{rotulo}.descricao", problemas)
        if id_prova is not None:
            if id_prova in ids:
                problemas.append(f"{rotulo}: id {id_prova!r} repetido")
            ids.add(id_prova)
        arquivos: list[ArquivoProvaK1] = []
        if not isinstance(obj["arquivos"], list) or not obj["arquivos"]:
            problemas.append(f"{rotulo}.arquivos: tem de ser lista nao vazia")
        else:
            for j, arq in enumerate(obj["arquivos"]):
                rot_arq = f"{rotulo}.arquivos[{j}]"
                a = _objeto(arq, CAMPOS_ARQUIVO_PROVA, rot_arq, problemas)
                if a is None:
                    continue
                caminho = _caminho_de_prova(a["caminho"], f"{rot_arq}.caminho", id_prova, problemas)
                sha = _digest(a["sha256"], f"{rot_arq}.sha256", problemas)
                if caminho is None or sha is None:
                    continue
                if caminho in caminhos:
                    problemas.append(f"{rot_arq}.caminho: {caminho!r} repetido")
                    continue
                antes = minusculos.setdefault(caminho.casefold(), caminho)
                if antes != caminho:
                    problemas.append(f"{rot_arq}.caminho: {caminho!r} colide com {antes!r} ignorando caixa")
                    continue
                caminhos.add(caminho)
                arquivos.append(ArquivoProvaK1(caminho, sha))
        esperado = _texto(obj["esperado"], f"{rotulo}.esperado", problemas)
        if (
            id_prova is not None
            and esperado is not None
            and esperado != f"{PROVAS_DIR}/{id_prova}/validacao/casos"
        ):
            problemas.append(f"{rotulo}.esperado: tem de ser {PROVAS_DIR}/{id_prova}/validacao/casos")
            esperado = None
        if id_prova is not None and descricao is not None and esperado is not None:
            provas.append(ProvaK1(id_prova, descricao, tuple(arquivos), esperado))
    return tuple(provas)


#: Parametros de contagem/prazo: inteiros > 0. Faixa escolhida pelo Maezo (ADR-0077 §7 item 2).
_PARAMETROS_INTEIROS: Final[frozenset[str]] = frozenset(
    {
        "revisao_inicial_decisoes",
        "revisao_inicial_prazo_dias",
        "limiar_reversao_min_decisoes",
        "conferencia_negativa_admin_decisoes",
    }
)
#: Parametros percentuais: numero finito em (0, 100] com no maximo 4 casas decimais.
_PARAMETROS_PERCENTUAIS: Final[frozenset[str]] = frozenset({"amostra_posterior_pct", "limiar_reversao_pct"})
_CASAS_DECIMAIS_PCT: Final = 4


def _inteiro_positivo(valor: object) -> bool:
    # JSON int estrito: `bool` e subclasse de int e `30.0` (float) sao recusados — o rco emite inteiros.
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def _percentual(valor: object) -> bool:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return False
    if isinstance(valor, int):
        # Int do JSON pode ser enorme (`10**400`): `math.isfinite` estouraria com OverflowError e o
        # leitor sairia por traceback, nao por problema. Comparacao int com int nao converte para float.
        return 0 < valor <= 100
    if not math.isfinite(valor) or not 0 < valor <= 100:
        return False
    # Casas decimais pelo `repr` (menor texto que reconstroi o float, o mesmo que o JSON escreve):
    # `Decimal(repr(5.0001))` tem expoente -4 (ok) e `Decimal(repr(5.00001))` -5 (recusado). Multiplicar
    # por 10000 e comparar com `round` erra em binario (0.0001 * 10000 != 1.0 em alguns valores).
    expoente = Decimal(repr(valor)).as_tuple().exponent
    return isinstance(expoente, int) and expoente >= -_CASAS_DECIMAIS_PCT


def _parametro_valido(chave: str, valor: object) -> bool:
    if chave in _PARAMETROS_INTEIROS:
        return _inteiro_positivo(valor)
    return _percentual(valor)


def _ler_ativacao(no: object, problemas: list[str]) -> tuple[frozenset[str], ParametrosAtivacao] | None:
    onde = "vocabulario.json: ativacao"
    bloco = _objeto(no, frozenset({"valores", "parametros"}), onde, problemas)
    if bloco is None:
        return None
    valores = bloco["valores"]
    if (
        not isinstance(valores, list)
        or len(set(map(str, valores))) != len(valores)
        or not all(isinstance(v, str) for v in valores)
        or set(valores) != ATIVACOES
    ):
        problemas.append(f"{onde}.valores tem de ser exatamente {sorted(ATIVACOES)}, sem repeticao")
        return None
    parametros = _objeto(bloco["parametros"], frozenset(PARAMETROS_ATIVACAO), f"{onde}.parametros", problemas)
    if parametros is None:
        return None
    ruins = [k for k in PARAMETROS_ATIVACAO if not _parametro_valido(k, parametros[k])]
    for chave in ruins:
        if chave in _PARAMETROS_INTEIROS:
            problemas.append(
                f"{onde}.parametros.{chave}: tem de ser inteiro JSON > 0 (sem booleano, sem decimal)"
            )
        else:
            problemas.append(
                f"{onde}.parametros.{chave}: tem de ser numero finito em (0, 100] com no maximo "
                f"{_CASAS_DECIMAIS_PCT} casas decimais (sem booleano)"
            )
    if ruins:
        return None
    return frozenset(valores), ParametrosAtivacao(**{k: parametros[k] for k in PARAMETROS_ATIVACAO})


def _lista_de_textos(no: object) -> list[str] | None:
    if not isinstance(no, list) or not no or not all(isinstance(v, str) and v for v in no):
        return None
    return [str(v) for v in no]


def _ler_vocabulario(raw: bytes, major: int, problemas: list[str]) -> VocabularioPacoteK1 | None:
    vocab = _carregar_json(raw, "vocabulario.json", problemas)
    if not isinstance(vocab, dict):
        if vocab is not None:
            problemas.append("vocabulario.json: tem de ser objeto")
        return None
    saidas = vocab.get("saidas_obrigatorias")
    if not isinstance(saidas, list) or not saidas or not all(isinstance(s, str) and s for s in saidas):
        problemas.append("vocabulario.json: 'saidas_obrigatorias' tem de ser lista nao vazia de textos")
        return None
    combinacao = vocab.get("combinacao")
    adversas = _lista_de_textos(combinacao.get("adversas") if isinstance(combinacao, dict) else None)
    exige = _lista_de_textos(
        combinacao.get("adversa_exige_preenchidos") if isinstance(combinacao, dict) else None
    )
    if adversas is None or exige is None:
        problemas.append(
            "vocabulario.json: 'combinacao.adversas' e 'combinacao.adversa_exige_preenchidos' tem de ser "
            "listas nao vazias de textos (contrato §6)"
        )
        return None
    if major == 1:
        return VocabularioPacoteK1(tuple(saidas), None, None, frozenset(adversas), tuple(exige))
    if "ativacao" not in vocab:
        problemas.append("vocabulario.json: formato MAJOR 2 exige o bloco 'ativacao' (contrato §7)")
        return None
    ativacao = _ler_ativacao(vocab["ativacao"], problemas)
    if ativacao is None:
        return None
    return VocabularioPacoteK1(tuple(saidas), ativacao[0], ativacao[1], frozenset(adversas), tuple(exige))


def _anexo_raw(manifesto: ManifestoK1, papel: str, k1_dir: Path, problemas: list[str]) -> bytes | None:
    anexo = manifesto.anexos.get(papel)
    if anexo is None:
        problemas.append(f"anexo {papel!r} ausente do manifesto")
        return None
    caminho = k1_dir / anexo.nome
    if caminho.is_symlink() or not caminho.is_file():
        problemas.append(f"{K1_DIR}/{anexo.nome}: anexo {papel!r} ausente (ou link simbolico)")
        return None
    raw = caminho.read_bytes()
    if sha256_prefixado(raw) != anexo.sha256:
        problemas.append(f"{K1_DIR}/{anexo.nome}: sha256 diverge do manifesto")
        return None
    return raw


def anexo_pinado(manifesto: ManifestoK1, papel: str, *, k1_dir: Path) -> bytes:
    """Bytes do anexo `papel` (`vocabulario`, `entradas`, `k6`) conferidos contra o sha256 do manifesto.

    Levanta `ManifestoK1InvalidoError`. Quem le o CONTEUDO do anexo e o seu leitor proprio (o
    dicionario de entradas: `maezo.platform.rco_entradas`)."""
    problemas: list[str] = []
    raw = _anexo_raw(manifesto, papel, k1_dir, problemas)
    if raw is None or problemas:
        raise ManifestoK1InvalidoError(problemas or [f"anexo {papel!r} invalido"])
    return raw


def carregar_vocabulario(manifesto: ManifestoK1, *, k1_dir: Path) -> VocabularioPacoteK1:
    """`vocabulario.json` pinado pelo manifesto, lido e tipado. Levanta `ManifestoK1InvalidoError`."""
    problemas: list[str] = []
    raw = _anexo_raw(manifesto, "vocabulario", k1_dir, problemas)
    vocab = _ler_vocabulario(raw, manifesto.major, problemas) if raw is not None else None
    if vocab is None or problemas:
        raise ManifestoK1InvalidoError(problemas or ["vocabulario.json invalido"])
    return vocab


def ids_de_decisao(raw: bytes, rotulo: str, *, exigir_prefixo: bool = True) -> list[str]:
    """Ids das `<decision>` de um DMN do K1, todos com prefixo `rco_`; ou `ManifestoK1InvalidoError`.

    `exigir_prefixo=False` (DMN de prova, que NAO pode usar `rco_`): so exige ao menos uma decisao com id.
    """
    try:
        root = _parse_xml_sem_dtd(raw)
    except (ET.ParseError, ValueError) as exc:
        raise ManifestoK1InvalidoError([f"{rotulo}: nao e DMN legivel ({type(exc).__name__})"]) from exc
    ids = [str(el.get("id") or "") for el in root.iter(f"{_DMN_NS}decision")]
    if not exigir_prefixo:
        if not ids or not all(ids):
            raise ManifestoK1InvalidoError([f"{rotulo}: DMN sem decisao com id"])
        return ids
    if not ids or any(not i.startswith(PREFIXO_DECISAO) for i in ids):
        raise ManifestoK1InvalidoError(
            [f"{rotulo}: tem de declarar decisoes com prefixo {PREFIXO_DECISAO!r}"]
        )
    return ids


def _texto_celula(entry: ET.Element) -> str:
    text_el = entry.find(f"{_DMN_NS}text")
    return (text_el.text or "").strip() if text_el is not None else ""


def _literal(texto: str) -> str | None:
    """Valor de um literal string FEEL; `""` para celula vazia/`null`; `None` para expressao."""
    if texto in ("", "null"):
        return ""
    casou = _LITERAL.match(texto)
    return re.sub(r"\\(.)", r"\1", casou.group(1)) if casou is not None else None


def _tabela(raw: bytes, decisao: DecisaoK1, problemas: list[str]) -> tuple[list[str], list[list[str]]] | None:
    """Colunas de saida e, por linha, o texto de cada outputEntry."""
    onde = decisao.caminho_consumidor
    try:
        root = _parse_xml_sem_dtd(raw)
    except (ET.ParseError, ValueError) as exc:
        problemas.append(f"{onde}: nao e DMN legivel ({type(exc).__name__})")
        return None
    elementos = list(root.iter(f"{_DMN_NS}decision"))
    if [str(el.get("id") or "") for el in elementos] != [decisao.id]:
        problemas.append(f"{onde}: tem de declarar exatamente a decisao {decisao.id!r} do manifesto")
        return None
    tabelas = list(elementos[0].iter(f"{_DMN_NS}decisionTable"))
    if len(tabelas) != 1:
        problemas.append(f"{onde}: decisao {decisao.id!r} tem de ser exatamente uma decisionTable")
        return None
    colunas = [str(o.get("name") or "") for o in tabelas[0].findall(f"{_DMN_NS}output")]
    linhas: list[list[str]] = []
    for i, regra in enumerate(tabelas[0].findall(f"{_DMN_NS}rule")):
        celulas = [_texto_celula(e) for e in regra.findall(f"{_DMN_NS}outputEntry")]
        if len(celulas) != len(colunas):
            problemas.append(f"{onde}: linha {i} tem {len(celulas)} outputEntry para {len(colunas)} saidas")
            return None
        linhas.append(celulas)
    return colunas, linhas


def _conferir_linhas(
    decisao: DecisaoK1,
    colunas: list[str],
    linhas: list[list[str]],
    vocab: VocabularioPacoteK1 | None,
    problemas: list[str],
) -> None:
    """`idRegra` emitidos == `regras[]` (§3) e linha adversa com os campos exigidos preenchidos (§6)."""
    onde = decisao.caminho_consumidor
    emitidos: set[str] = set()
    for i, celulas in enumerate(linhas):
        linha = dict(zip(colunas, celulas, strict=True))
        if SAIDA_ID_REGRA in linha:
            valor = _literal(linha[SAIDA_ID_REGRA])
            if valor is None:
                problemas.append(f"{onde}: linha {i}: {SAIDA_ID_REGRA} nao e literal string")
            elif valor:
                emitidos.add(valor)
        decisao_linha = _literal(linha["decisao"]) if "decisao" in linha else ""
        if decisao_linha is None:
            # Expressao em `decisao`: nao da para saber se a linha e adversa, entao nao se confere o
            # que ela exige. Fail-closed: o K1 so emite decisao como literal.
            problemas.append(f"{onde}: linha {i}: decisao nao e literal string")
            continue
        if vocab is None or decisao_linha not in vocab.adversas:
            continue
        vazios = [c for c in vocab.adversa_exige_preenchidos if c in linha and not _literal(linha[c])]
        if vazios:
            problemas.append(
                f"{onde}: linha {i} adversa ({_literal(linha['decisao'])}) sem {vazios} preenchido(s) "
                "(vocabulario.json combinacao.adversa_exige_preenchidos, contrato §6)"
            )
    declarados = set(decisao.regras)
    if emitidos != declarados:
        problemas.append(
            f"{onde}: idRegra emitidos pela tabela diferem de regras[] do manifesto (contrato §3): "
            f"so na tabela {sorted(emitidos - declarados)}, so no manifesto {sorted(declarados - emitidos)}"
        )


# --- guarda da tolerancia do prazo de conta (MZO-TOL, PROG-COORD-22) -------------------------------
#: Entrada K1 dos dias alem do prazo de apresentacao do contrato. A tolerancia sobre esse prazo mora SO
#: na configuracao do Maezo (montador), nunca na tabela: uma linha que LIBERA uma faixa positiva LIMITADA
#: desta entrada (ex.: `[1..30]`) seria a mesma tolerancia dentro do DMN, e as duas se somariam.
ENTRADA_DIAS_ALEM_PRAZO: Final[str] = "diasAlemDoPrazoContratual"
_DECISAO_QUE_LIBERA: Final[str] = "liberar"
_NUM: Final[str] = r"-?\d+(?:\.\d+)?"
_UNARIO_NUMERO: Final[re.Pattern[str]] = re.compile(rf"^{_NUM}$")
_UNARIO_COMPARACAO: Final[re.Pattern[str]] = re.compile(rf"^(<=|<|>=|>)\s*({_NUM})$")
_UNARIO_INTERVALO: Final[re.Pattern[str]] = re.compile(rf"^[\[\(\]]\s*({_NUM})\s*\.\.\s*({_NUM})\s*[\]\)\[]$")


def _partes_unarias(texto: str) -> list[str] | None:
    """Os testes unarios separados por virgula no nivel zero (fora de aspas e parenteses/colchetes)."""
    partes: list[str] = []
    atual: list[str] = []
    nivel = 0
    em_aspas = False
    escape = False
    for ch in texto:
        if em_aspas:
            atual.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                em_aspas = False
            continue
        if ch == '"':
            em_aspas = True
        elif ch in "([":
            nivel += 1
        elif ch in ")]":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append("".join(atual).strip())
            atual = []
            continue
        atual.append(ch)
    if em_aspas:
        return None
    partes.append("".join(atual).strip())
    return partes


def _faixa_positiva_limitada(texto: str) -> bool | None:
    """`True` se o teste unario aceita algum valor > 0 com limite superior finito; `False` se nao;
    `None` se a celula nao e lida (expressao, `not(...)`, variavel): quem chama trata como achado."""
    if texto in ("", "-", "null"):
        return False
    partes = _partes_unarias(texto)
    if partes is None:
        return None
    achou = False
    for parte in partes:
        if parte == "null":
            continue
        if _UNARIO_NUMERO.fullmatch(parte):
            achou = achou or Decimal(parte) > 0
            continue
        comparacao = _UNARIO_COMPARACAO.fullmatch(parte)
        if comparacao is not None:
            # `>`/`>=` nao tem limite superior: e a faixa "fora do prazo", nunca uma tolerancia.
            achou = achou or (comparacao.group(1) in ("<", "<=") and Decimal(comparacao.group(2)) > 0)
            continue
        intervalo = _UNARIO_INTERVALO.fullmatch(parte)
        if intervalo is not None:
            achou = achou or Decimal(intervalo.group(2)) > 0
            continue
        return None
    return achou


#: A entrada como token FEEL (nao parte de outro nome).
_TOKEN_DIAS_ALEM: Final[re.Pattern[str]] = re.compile(
    rf"(?<![A-Za-z0-9_]){ENTRADA_DIAS_ALEM_PRAZO}(?![A-Za-z0-9_])"
)
#: Hit policies em que a ordem das linhas nao muda o resultado (o DMN sem `hitPolicy` e UNIQUE).
_HIT_POLICIES_SEM_ORDEM: Final[frozenset[str]] = frozenset({"UNIQUE", "ANY"})


def tolerancia_prazo_conta_no_dmn(raw: bytes, rotulo: str) -> list[str]:
    """Achados de tolerancia embutida: linhas que LIBERAM uma faixa positiva limitada de
    `diasAlemDoPrazoContratual`. Lista vazia = a tabela nao tolera (ou nao usa a entrada).

    Fail-closed (o gate do pin recusa; o montador zera a tolerancia do Maezo). Tambem sao achados:
    - tabela que usa a entrada com `hitPolicy` diferente de UNIQUE/ANY: com FIRST, PRIORITY, RULE ORDER
      ou COLLECT a ordem ou a agregacao das linhas pode liberar uma faixa que a celula sozinha nao mostra
      (ex.: FIRST com `> 30` -> glosar antes de `> 0` -> liberar);
    - expressao de entrada que cita a entrada sem ser o nome puro (`(diasAlemDoPrazoContratual)`,
      `diasAlemDoPrazoContratual - 0`), e celula de outra coluna que a cita;
    - DMN ilegivel, celula que nao se le como teste unario numerico, linha sem as celulas da tabela ou
      saida `decisao` nao literal numa linha de faixa positiva limitada.
    So biblioteca padrao (o gate o le).
    """
    try:
        root = _parse_xml_sem_dtd(raw)
    except (ET.ParseError, ValueError) as exc:
        return [f"{rotulo}: nao e DMN legivel ({type(exc).__name__})"]
    achados: list[str] = []
    for tabela in root.iter(f"{_DMN_NS}decisionTable"):
        expressoes: list[str] = []
        for coluna_in in tabela.findall(f"{_DMN_NS}input"):
            expressao = coluna_in.find(f"{_DMN_NS}inputExpression")
            expressoes.append(_texto_celula(expressao) if expressao is not None else "")
        exatas = [i for i, e in enumerate(expressoes) if e == ENTRADA_DIAS_ALEM_PRAZO]
        indiretas = [i for i, e in enumerate(expressoes) if i not in exatas and _TOKEN_DIAS_ALEM.search(e)]
        regras = tabela.findall(f"{_DMN_NS}rule")
        celulas_citam = any(
            _TOKEN_DIAS_ALEM.search(_texto_celula(e))
            for regra in regras
            for e in regra.findall(f"{_DMN_NS}inputEntry")
        )
        if not exatas and not indiretas and not celulas_citam:
            continue
        onde_tabela = f"{rotulo}: tabela {tabela.get('id') or 'sem id'}"
        achados.extend(
            f"{onde_tabela}: entrada {i} cita {ENTRADA_DIAS_ALEM_PRAZO} sem ser o nome puro "
            f"({expressoes[i]!r})"
            for i in indiretas
        )
        politica = str(tabela.get("hitPolicy") or "UNIQUE").strip().upper()
        if politica not in _HIT_POLICIES_SEM_ORDEM:
            achados.append(
                f"{onde_tabela}: hitPolicy {politica} com {ENTRADA_DIAS_ALEM_PRAZO}; so UNIQUE ou ANY "
                "(a ordem das linhas poderia liberar uma faixa)"
            )
        saidas = [str(o.get("name") or "") for o in tabela.findall(f"{_DMN_NS}output")]
        coluna_decisao = saidas.index("decisao") if "decisao" in saidas else None
        for i, regra in enumerate(regras):
            onde = f"{rotulo}: linha {i} ({regra.get('id') or 'sem id'})"
            entradas = regra.findall(f"{_DMN_NS}inputEntry")
            saidas_linha = regra.findall(f"{_DMN_NS}outputEntry")
            if len(entradas) != len(expressoes) or len(saidas_linha) != len(saidas):
                achados.append(f"{onde}: celulas diferentes das colunas da tabela")
                continue
            if any(
                j not in exatas and _TOKEN_DIAS_ALEM.search(_texto_celula(e)) for j, e in enumerate(entradas)
            ):
                achados.append(f"{onde}: celula de outra coluna cita {ENTRADA_DIAS_ALEM_PRAZO}")
            decisao = (
                _literal(_texto_celula(saidas_linha[coluna_decisao])) if coluna_decisao is not None else None
            )
            if decisao is not None and decisao != _DECISAO_QUE_LIBERA:
                continue
            for coluna in exatas:
                faixa = _faixa_positiva_limitada(_texto_celula(entradas[coluna]))
                if faixa is None:
                    achados.append(f"{onde}: {ENTRADA_DIAS_ALEM_PRAZO} nao e teste unario numerico legivel")
                elif faixa:
                    achados.append(
                        f"{onde}: libera faixa positiva limitada de {ENTRADA_DIAS_ALEM_PRAZO} "
                        "(tolerancia embutida no DMN; a do Maezo se somaria a ela)"
                    )
    return achados


def ativacao_nao_suportada(manifesto: ManifestoK1) -> list[str]:
    """Regras do pacote cuja ativacao este consumidor ainda nao executa. Lista vazia = ok.

    MAJOR 2 (contrato §7): so `ativacao: plena` sem `exige_aditivo` tem significado ligado hoje. As
    demais (`conferida`, `em_correcao`, restricao com aditivo) dependem da Onda 2 item 5 (ativacao por
    classe de risco); ate la o pacote inteiro e recusado, para que nenhum efeito adverso rode sem a
    regra de ativacao que o contrato manda aplicar.
    """
    if manifesto.major < 2:
        return []
    problemas: list[str] = []
    for decisao in manifesto.decisoes.values():
        for regra in decisao.regras.values():
            if regra.ativacao != "plena" or regra.exige_aditivo is not False:
                problemas.append(
                    f"{decisao.id}/{regra.id_regra}: ativacao={regra.ativacao!r}, "
                    f"exige_aditivo={regra.exige_aditivo!r} — semantica ainda nao ligada no consumidor "
                    "(Onda 2 item 5, ativacao por classe de risco); so 'plena' sem aditivo e aceito"
                )
    return problemas


def provas_ausentes_no_pin(manifesto: ManifestoK1) -> list[str]:
    """Motivos para recusar PINAR o pacote por falta de provas do motor. Lista vazia = ok.

    Decisao da ROOT (16/11/2026, contrato-k1.md §11 item 12): pin real so com `provas[]` executadas com
    sucesso no CIB Seven. Formato abaixo de 2.1.0 (1.x e 2.0) nao tem provas e e recusado no pin; 2.1+
    com `provas[]` vazia tambem (o leitor ja recusa a lista vazia; aqui fica a mesma recusa no pin,
    sem depender disso). O leitor continua LENDO 1.x/2.0 para diagnostico: a recusa e so na hora de
    pinar (gate `verify_rco_rules_pin` e runtime `rco_pin.load_rco_rules_pin`). A execucao das provas
    no motor e de `rco_provas.executar_provas_do_pin`, no shard de integracao.
    """
    major, minor, _ = (int(p) for p in manifesto.formato_manifesto.split("."))
    if (major, minor) < FORMATO_COM_PROVAS:
        minimo = ".".join(str(n) for n in FORMATO_COM_PROVAS)
        return [
            f"formato_manifesto {manifesto.formato_manifesto} nao tem provas[]: o pin exige {minimo}.0 ou "
            "maior, com provas do motor executadas com sucesso no CIB Seven (contrato-k1.md §11 item 12)"
        ]
    if not manifesto.provas:
        return [
            "provas[] vazia: o pin exige provas do motor executadas com sucesso no CIB Seven "
            "(contrato-k1.md §11 item 12)"
        ]
    return []


def nomes_k1_declarados(raw_manifesto: bytes | None) -> set[str]:
    """Nomes aceitos em `k1/`: o manifesto e os anexos que ele NOMEIA (leitura leniente, so nomes).

    A validacao do manifesto e de `ler_manifesto`; aqui so se evita repetir uma falha ja reportada
    arquivo a arquivo quando o manifesto e invalido.
    """
    nomes = {K1_MANIFESTO_NOME}
    if raw_manifesto is None:
        return nomes
    try:
        dados = json_estrito(raw_manifesto)
    except ValueError:
        return nomes
    if isinstance(dados, dict):
        for secao, _campo in ANEXOS.values():
            bloco = dados.get(secao)
            nome = bloco.get("path") if isinstance(bloco, dict) else None
            if isinstance(nome, str) and _NOME_PLANO.fullmatch(nome) and nome not in (".", ".."):
                nomes.add(nome)
        provas = dados.get(CAMPO_TOPO_PROVAS) if _formato_tem_provas(dados) else None
        for prova in provas if isinstance(provas, list) else []:
            arquivos = prova.get("arquivos") if isinstance(prova, dict) else None
            for arq in arquivos if isinstance(arquivos, list) else []:
                caminho = arq.get("caminho") if isinstance(arq, dict) else None
                if (
                    isinstance(caminho, str)
                    and caminho.startswith(f"{PROVAS_DIR}/")
                    and not any(p in ("", ".", "..") for p in caminho.split("/"))
                ):
                    nomes.add(caminho)  # relativo a k1/, com barras: so o que o manifesto nomeia
    return nomes


def verificar_pastas(
    *, rules_dir: Path, k1_dir: Path, listados_rco: set[str], nomes_k1: set[str]
) -> list[tuple[str, str]]:
    """Varredura fail-closed das duas pastas do pacote. Devolve `(codigo, detalhe)`; vazia = ok.

    `rules_dir` (`spec/processes/dmn/rco/`) aceita so `README.md` e os nomes em `listados_rco`;
    `k1_dir` (`config/integrations/rco/k1/`) so os de `nomes_k1` (vazio sem pacote). Pasta ausente e
    pasta vazia. Link simbolico (na pasta ou dentro dela), subpasta e arquivo solto reprovam.
    """
    achados: list[tuple[str, str]] = []
    for pasta, rotulo, aceitos in (
        (rules_dir, RULES_DIR, set(listados_rco) | {README_NOME}),
        (k1_dir, K1_DIR, set(nomes_k1)),
    ):
        if pasta.is_symlink():
            achados.append(("link-simbolico", f"{rotulo} e link simbolico; o pin exige caminho real"))
            continue
        if not pasta.exists():
            continue
        if not pasta.is_dir():
            achados.append(("pasta-invalida", f"{rotulo} existe mas nao e pasta"))
            continue
        provas_aceitas = {n for n in aceitos if "/" in n} if pasta == k1_dir else set()
        pastas_de_provas = {a.rsplit("/", i)[0] for a in provas_aceitas for i in range(1, a.count("/") + 1)}
        for caminho in sorted(pasta.rglob("*")):
            rel_k1 = caminho.relative_to(pasta).as_posix()
            rel = f"{rotulo}/{rel_k1}"
            if caminho.is_symlink():
                achados.append(("link-simbolico", f"{rel} e link simbolico; recusado"))
            elif rel_k1 in pastas_de_provas or rel_k1 in provas_aceitas:
                continue  # pasta ancestral de prova declarada, ou arquivo de prova declarado
            elif pasta == k1_dir and rel_k1.startswith(f"{PROVAS_DIR}/") and caminho.is_file():
                achados.append(
                    (
                        "prova-fora-do-manifesto",
                        f"{rel} esta em {PROVAS_DIR}/ e nao em provas[] (contrato §4 item 7)",
                    )
                )
            elif caminho.is_dir():
                achados.append(("subpasta-nao-permitida", f"{rel}: {rotulo}/ e plana (ADR-0075 b)"))
            elif caminho.parent == pasta and caminho.name in aceitos:
                continue
            elif pasta == rules_dir and caminho.suffix == ".dmn":
                achados.append(
                    (
                        "dmn-nao-listado",
                        f"{rel} nao esta no pin (nem em artifacts nem em exemplos_sinteticos); "
                        "a pasta recebe so arquivos do K1 via atualizacao do pin",
                    )
                )
            else:
                achados.append(
                    (
                        "arquivo-nao-listado",
                        f"{rel} nao esta no pin nem no manifesto (exemplos do pacote ficam fora do lock)",
                    )
                )
    return achados


def verificar_provas(manifesto: ManifestoK1, *, k1_dir: Path) -> list[str]:
    """Contrato §4 item 7: cada arquivo de `provas[]` existe (sem link), sob `provas/<id>/`, com o sha256 do
    manifesto. Arquivo a mais em `provas/` e achado de `verificar_pastas`. Devolve os problemas."""
    problemas: list[str] = []
    for prova in manifesto.provas:
        for arq in prova.arquivos:
            caminho = k1_dir.joinpath(*arq.caminho.split("/"))
            if caminho.is_symlink() or not caminho.is_file():
                problemas.append(f"{K1_DIR}/{arq.caminho}: arquivo de prova ausente (ou link simbolico)")
            elif sha256_prefixado(caminho.read_bytes()) != arq.sha256:
                problemas.append(f"{K1_DIR}/{arq.caminho}: sha256 diverge do manifesto")
    return problemas


def verificar_pacote(manifesto: ManifestoK1, *, k1_dir: Path, rules_dir: Path) -> list[str]:
    """Confere os bytes que o manifesto descreve. Devolve TODOS os problemas (lista vazia = ok).

    `k1_dir`: pasta com os anexos (`config/integrations/rco/k1/`). `rules_dir`: pasta dos DMN do
    consumidor (`spec/processes/dmn/rco/`). Cada anexo e cada DMN e re-hasheado; cada DMN declara a
    decisao do manifesto; suas colunas de saida contem as obrigatorias de `vocabulario.json` e
    nenhuma fora do `saidas[]` da decisao.
    """
    problemas: list[str] = []
    anexos_raw: dict[str, bytes] = {}
    for papel in ANEXOS:
        raw = _anexo_raw(manifesto, papel, k1_dir, problemas)
        if raw is not None:
            anexos_raw[papel] = raw

    obrigatorias: tuple[str, ...] | None = None
    vocab: VocabularioPacoteK1 | None = None
    if "vocabulario" in anexos_raw:
        vocab = _ler_vocabulario(anexos_raw["vocabulario"], manifesto.major, problemas)
        if vocab is not None:
            obrigatorias = vocab.saidas_obrigatorias
            if vocab.ativacoes is not None:
                for decisao in manifesto.decisoes.values():
                    for regra in decisao.regras.values():
                        if regra.ativacao not in vocab.ativacoes:
                            problemas.append(
                                f"{decisao.id}/{regra.id_regra}: ativacao {regra.ativacao!r} fora de "
                                "vocabulario.json ativacao.valores"
                            )

    for decisao in manifesto.decisoes.values():
        onde = decisao.caminho_consumidor
        caminho = rules_dir / PurePosixPath(onde).name
        if caminho.is_symlink() or not caminho.is_file():
            problemas.append(f"{onde}: arquivo do manifesto ausente (ou link simbolico)")
            continue
        raw = caminho.read_bytes()
        if sha256_prefixado(raw) != decisao.sha256:
            problemas.append(f"{onde}: sha256 diverge do manifesto")
            continue
        tabela = _tabela(raw, decisao, problemas)
        if tabela is None:
            continue
        colunas, linhas = tabela
        _conferir_linhas(decisao, colunas, linhas, vocab, problemas)
        repetidas = sorted({c for c in colunas if colunas.count(c) > 1})
        if repetidas:
            problemas.append(f"{onde}: coluna de saida repetida {repetidas}")
        fora = [c for c in colunas if c not in decisao.saidas]
        if fora:
            problemas.append(
                f"{onde}: coluna(s) de saida {fora} fora do saidas[] da decisao no manifesto (contrato §6)"
            )
        nao_emitidas = [s for s in decisao.saidas if s not in colunas]
        if nao_emitidas:
            problemas.append(f"{onde}: saidas[] do manifesto declara {nao_emitidas}, ausentes da tabela")
        if obrigatorias is not None:
            faltam = [s for s in obrigatorias if s not in colunas]
            if faltam:
                problemas.append(f"{onde}: faltam as saidas obrigatorias {faltam} (vocabulario.json)")
    problemas.extend(verificar_provas(manifesto, k1_dir=k1_dir))
    return problemas


__all__ = [
    "ANEXOS",
    "ATIVACOES",
    "PARAMETROS_ATIVACAO",
    "ParametrosAtivacao",
    "VocabularioPacoteK1",
    "carregar_vocabulario",
    "CAMPOS_REGRA_POR_MAJOR",
    "FORMATOS_SUPORTADOS",
    "K1_DIR",
    "K1_MANIFESTO_NOME",
    "K1_MANIFESTO_PATH",
    "PREFIXO_DECISAO",
    "RULES_DIR",
    "AnexoK1",
    "ArquivoProvaK1",
    "CAMPO_TOPO_PROVAS",
    "FORMATO_COM_PROVAS",
    "PROVAS_DIR",
    "ProvaK1",
    "verificar_provas",
    "DecisaoK1",
    "ManifestoK1",
    "ManifestoK1InvalidoError",
    "RegraK1",
    "bytes_canonicos",
    "is_digest",
    "ler_manifesto",
    "sha256_prefixado",
    "verificar_pacote",
    "SAIDA_ID_REGRA",
    "ativacao_nao_suportada",
    "ENTRADA_DIAS_ALEM_PRAZO",
    "tolerancia_prazo_conta_no_dmn",
    "provas_ausentes_no_pin",
    "ids_de_decisao",
    "json_estrito",
    "nomes_k1_declarados",
    "verificar_pastas",
]
