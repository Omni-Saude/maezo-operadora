"""Leitor do manifesto do pacote K1 do rco (ADR-0075 secao (b); contrato-k1.md §3-§6, itens 1-6 do §11).

O QUE ISTO E. O rco publica o K1 como `build/pacote/k1/` (contrato `docs/programa/contrato-k1.md` no
repositorio `regras-contratos-operadora`): `manifesto.json` em bytes canonicos, `vocabulario.json`,
`entradas.json`, `k6-filas.json`, `dmn/<arquivo>.dmn` e `exemplos/<id>.json`. O Maezo vendoriza:

- o manifesto e os tres anexos em `config/integrations/rco/k1/` (pasta plana, ao lado do pin);
- cada DMN em `spec/processes/dmn/rco/<arquivo>.dmn` (`caminho_consumidor`, plano — §11 item 4);
- NUNCA os exemplos: sao material de teste do pacote, fora do lock (§3, §11 item 5).

Este modulo e o UNICO leitor do manifesto. O gate de CI (`scripts/ci/verify_rco_rules_pin.py`) e o
carregador de runtime (`maezo.tools.workers.rco_pin`) o usam, para que nao haja dois formatos.
So biblioteca padrao (mais `maezo.platform.xml_safe`, tambem so biblioteca padrao) e sem rede.

FAIL-CLOSED (contrato §4 e §5):
- digest em toda parte no formato `sha256:<64 hex minusculos>`; hex cru e recusado (§1 — o hex cru
  dispara o detector de segredos; mesma notacao do K2, HEL-3a);
- `formato_manifesto` com MAJOR 1 ou 2 e MINOR ate o que este leitor conhece; outro e recusado
  (MAJOR 2: `regras[].ativacao` em conjunto fechado e `exige_aditivo` booleano, sem
  `ratificavel_automatico`; o resto da forma do 2.0.0 segue a do 1.0.0 ate o rco publicar o contrato);
- `origem.sujo` presente (pacote de workspace sujo) e recusado;
- campos EXATOS no topo, em cada objeto aninhado, em cada decisao e em cada regra: campo a mais ou a
  menos e erro (§4 item 7; um MINOR novo de formato exige atualizar este leitor antes de pinar);
- bytes canonicos (`sort_keys`, `ensure_ascii=False`, separadores sem espaco), chave JSON repetida
  recusada;
- id de decisao com o prefixo `rco_`, igual ao `<decision>` do arquivo; `idRegra` no padrao;
- anexos (`vocabulario.json`, `entradas.json`, `k6-filas.json`) com o sha256 do manifesto;
- colunas de saida da tabela: as obrigatorias de `vocabulario.json` presentes, e qualquer coluna fora
  do `saidas[]` daquela decisao recusada (§6 "Saidas extras", §11 item 6).

Este modulo NAO decide regra de negocio: so le e confere bytes e forma. O vocabulario fechado das
saidas e conferido pelo lint `platform/validation/vocabulario_saida.py`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final

from maezo.platform.xml_safe import parse_xml_without_dtd

#: Pasta (relativa a raiz do repo) do manifesto K1 e dos anexos vendorizados. Plana.
K1_DIR: Final[str] = "config/integrations/rco/k1"
#: Nome do manifesto dentro de `K1_DIR`; `manifest_pin.path` do lock e exatamente `K1_MANIFESTO_PATH`.
K1_MANIFESTO_NOME: Final[str] = "manifesto.json"
K1_MANIFESTO_PATH: Final[str] = f"{K1_DIR}/{K1_MANIFESTO_NOME}"
#: Pasta do consumidor para os DMN do K1 (= `lock_format.rules_dir`).
RULES_DIR: Final[str] = "spec/processes/dmn/rco"
PREFIXO_DECISAO: Final[str] = "rco_"

#: MAJOR do `formato_manifesto` que este leitor suporta -> o maior MINOR que ele conhece (§5).
#: MAJOR 2 (PROG-RCO-8 no rco, contrato ainda nao publicado): por regra, sai `ratificavel_automatico`
#: e entram `ativacao` (conjunto fechado) e `exige_aditivo` (booleano). O resto da forma e o do
#: MAJOR 1 ate o contrato 2.0.0 estabilizar; campo a mais ou a menos continua recusado.
FORMATOS_SUPORTADOS: Final[Mapping[int, int]] = MappingProxyType({1: 0, 2: 0})
#: Valores de `regras[].ativacao` (MAJOR 2). Valor fora do conjunto reprova (fail-closed).
ATIVACOES: Final[frozenset[str]] = frozenset({"plena", "conferida", "em_correcao"})

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
_SEMVER: Final[re.Pattern[str]] = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_ID_REGRA: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NOME_PLANO: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")
_DMN_NS: Final[str] = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


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


@dataclass(frozen=True, slots=True)
class AnexoK1:
    """Um anexo pinado pelo manifesto (`vocabulario.json`, `entradas.json`, `k6-filas.json`)."""

    papel: str
    nome: str  # nome plano do arquivo dentro de k1/
    sha256: str


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

    def decisao_por_caminho(self) -> dict[str, DecisaoK1]:
        return {d.caminho_consumidor: d for d in self.decisoes.values()}


class _ChaveDuplicadaError(ValueError):
    pass


def _sem_chave_duplicada(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for chave, valor in pares:
        if chave in out:
            raise _ChaveDuplicadaError(chave)
        out[chave] = valor
    return out


def _carregar_json(raw: bytes, rotulo: str, problemas: list[str]) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_sem_chave_duplicada)
    except _ChaveDuplicadaError as exc:
        problemas.append(f"{rotulo}: chave JSON repetida {exc.args[0]!r}")
    except (UnicodeDecodeError, ValueError) as exc:
        problemas.append(f"{rotulo}: JSON ilegivel ({type(exc).__name__})")
    return None


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
            if regra["ativacao"] not in ATIVACOES:
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
    if not isinstance(obj["entradas"], list):
        problemas.append(f"{onde}.entradas: tem de ser lista")
    else:
        for j, entrada in enumerate(obj["entradas"]):
            _objeto(entrada, CAMPOS_ENTRADA, f"{onde}.entradas[{j}]", problemas)
    saidas = _ler_saidas(obj["saidas"], f"{onde}.saidas", problemas)
    regras = _ler_regras(obj["regras"], f"{onde}.regras", major, problemas)
    if (
        did is None
        or original is None
        or arquivo is None
        or caminho is None
        or sha is None
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
    return DecisaoK1(did, arquivo, caminho, sha, saidas, MappingProxyType(regras))


def ler_manifesto(raw: bytes) -> ManifestoK1:
    """Le e valida a FORMA do manifesto K1. Levanta `ManifestoK1InvalidoError` com todos os motivos."""
    problemas: list[str] = []
    dados = _carregar_json(raw, "manifesto", problemas)
    if dados is None:
        raise ManifestoK1InvalidoError(problemas)
    if bytes_canonicos(dados) != raw:
        problemas.append("manifesto: bytes nao sao os canonicos (contrato §3); nao reserialize o arquivo")
    if isinstance(dados, dict) and isinstance(dados.get("origem"), dict) and "sujo" in dados["origem"]:
        _ler_origem(dados["origem"], problemas)  # registra a recusa antes da checagem de forma
        raise ManifestoK1InvalidoError(problemas)
    topo = _objeto(dados, CAMPOS_TOPO, "manifesto", problemas)
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
                exemplos.append(str(exemplo["path"]))

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
    )


def _saidas_obrigatorias(raw: bytes, problemas: list[str]) -> tuple[str, ...] | None:
    vocab = _carregar_json(raw, "vocabulario.json", problemas)
    if vocab is None:
        return None
    saidas = vocab.get("saidas_obrigatorias") if isinstance(vocab, dict) else None
    if not isinstance(saidas, list) or not saidas or not all(isinstance(s, str) and s for s in saidas):
        problemas.append("vocabulario.json: 'saidas_obrigatorias' tem de ser lista nao vazia de textos")
        return None
    return tuple(saidas)


def _colunas_de_saida(raw: bytes, decisao: DecisaoK1, problemas: list[str]) -> list[str] | None:
    onde = decisao.caminho_consumidor
    try:
        root = parse_xml_without_dtd(raw)
    except Exception as exc:  # ParseError / DtdRefusedError
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
    return [str(o.get("name") or "") for o in tabelas[0].findall(f"{_DMN_NS}output")]


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
        anexo = manifesto.anexos.get(papel)
        if anexo is None:
            problemas.append(f"anexo {papel!r} ausente do manifesto")
            continue
        caminho = k1_dir / anexo.nome
        if caminho.is_symlink() or not caminho.is_file():
            problemas.append(f"{K1_DIR}/{anexo.nome}: anexo {papel!r} ausente (ou link simbolico)")
            continue
        raw = caminho.read_bytes()
        if sha256_prefixado(raw) != anexo.sha256:
            problemas.append(f"{K1_DIR}/{anexo.nome}: sha256 diverge do manifesto")
            continue
        anexos_raw[papel] = raw

    obrigatorias: tuple[str, ...] | None = None
    if "vocabulario" in anexos_raw:
        obrigatorias = _saidas_obrigatorias(anexos_raw["vocabulario"], problemas)

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
        colunas = _colunas_de_saida(raw, decisao, problemas)
        if colunas is None:
            continue
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
    return problemas


__all__ = [
    "ANEXOS",
    "ATIVACOES",
    "CAMPOS_REGRA_POR_MAJOR",
    "FORMATOS_SUPORTADOS",
    "K1_DIR",
    "K1_MANIFESTO_NOME",
    "K1_MANIFESTO_PATH",
    "PREFIXO_DECISAO",
    "RULES_DIR",
    "AnexoK1",
    "DecisaoK1",
    "ManifestoK1",
    "ManifestoK1InvalidoError",
    "RegraK1",
    "bytes_canonicos",
    "is_digest",
    "ler_manifesto",
    "sha256_prefixado",
    "verificar_pacote",
]
