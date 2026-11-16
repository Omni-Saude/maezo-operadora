"""Provas de comportamento do motor do pacote K1 (contrato-k1.md §3 `provas[]`, §11 item 12).

O rco publica, no `formato_manifesto` 2.1.0, provas: um DMN de teste e casos (`entradas` + `esperado`)
sob `k1/provas/<id>/`. Antes de pinar o pacote o consumidor roda cada caso no PROPRIO motor (CIB Seven)
e recusa o pin se algum caso divergir do `esperado`. Este modulo faz so isso: carrega os casos pelos
arquivos que o manifesto lista, avalia cada um num `Avaliador` e devolve as divergencias. Nao ha
efeito em runtime: ninguem o chama no caminho de decisao. `executar_provas_do_pin` le o pin e roda todas as
provas do pacote pinado; o teste de integracao que o chama roda no shard de integracao do PR que muda o
pin (decisao da ROOT, 16/11/2026: pin real so com provas executadas com sucesso no CIB Seven).

O QUE "casa" SIGNIFICA (fixture `teste-unario-booleano`, README do rco): cada decisao de teste tem
hit policy FIRST com duas linhas. A linha 1 e a celula testada e emite `resultado = "casa"`; a linha 2
e `-` (qualquer entrada) e emite `resultado = "nao_casa"`. Logo "casa" = a celula testada casou com a
entrada (a linha 1 foi a escolhida); "nao_casa" = so a linha de fechamento casou. Operacionalmente:
a avaliacao devolve EXATAMENTE uma linha e o texto da sua coluna `resultado` e igual a
`esperado.resultado`. Zero linhas, mais de uma, coluna ausente ou valor diferente e divergencia.

Fail-closed: caso malformado, decisao ausente do DMN, erro do motor ou resultado fora de
{casa, nao_casa} contam como divergencia; nunca se ajusta o `esperado` ao que o motor devolveu.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

from maezo.platform import rco_manifesto as k1
from maezo.tools.workers import rco_pin
from maezo.tools.workers.dmn_transport import _from_camunda_var, _to_camunda_vars

RESULTADOS: Final[frozenset[str]] = frozenset({"casa", "nao_casa"})
SAIDA_RESULTADO: Final[str] = "resultado"
BASE_PROVAS: Final[str] = k1.PROVAS_DIR


class Avaliador(Protocol):
    """Avalia uma decisao (id dentro do DMN da prova) com as entradas; devolve as linhas da tabela."""

    async def avaliar(self, decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CasoProva:
    prova: str
    arquivo: str  # caminho do arquivo de casos, relativo a `k1/`
    dmn: str  # `dmn` do arquivo de casos (relativo a `provas/<id>/`)
    decisao: str
    id_caso: str
    entradas: Mapping[str, Any]
    esperado: str  # `casa` | `nao_casa`


@dataclass(frozen=True, slots=True)
class Divergencia:
    prova: str
    caso: str  # `<arquivo>#<id do caso>`
    esperado: str
    obtido: str  # `casa`, `nao_casa`, ou o motivo (`erro: ...`, `0 linhas`, ...)

    def render(self) -> str:
        return f"prova {self.prova} {self.caso}: esperado {self.esperado!r}, obtido {self.obtido!r}"


def ler_arquivo_de_prova(k1_dir: Path, arq: k1.ArquivoProvaK1) -> bytes:
    """Bytes do arquivo da prova, lidos UMA vez: sem link simbolico (nem nos ancestrais sob `k1_dir`) e com
    o sha256 do manifesto. Quem usa os bytes devolvidos usa exatamente os que foram conferidos."""
    caminho = k1_dir.joinpath(*arq.caminho.split("/"))
    atual = caminho
    while atual != k1_dir and atual != atual.parent:
        if atual.is_symlink():
            raise k1.ManifestoK1InvalidoError([f"{arq.caminho}: link simbolico recusado"])
        atual = atual.parent
    try:
        raw = caminho.read_bytes()
    except OSError as exc:
        raise k1.ManifestoK1InvalidoError([f"{arq.caminho}: ausente ou ilegivel"]) from exc
    if k1.sha256_prefixado(raw) != arq.sha256:
        raise k1.ManifestoK1InvalidoError([f"{arq.caminho}: sha256 diverge do manifesto"])
    return raw


@dataclass(frozen=True, slots=True)
class ProvaPreparada:
    """Prova ja conferida: DMN (bytes verificados), ids de decisao e casos."""

    prova: k1.ProvaK1
    dmns: Mapping[str, bytes]  # caminho do DMN relativo a `provas/<id>/` -> bytes
    decisoes: frozenset[str]  # ids `<decision>` de todos os DMN da prova
    casos: tuple[CasoProva, ...]


def preparar_prova(prova: k1.ProvaK1, *, k1_dir: Path) -> ProvaPreparada:
    """Le (uma vez, com sha256 e sem link), valida e devolve a prova. Levanta `ManifestoK1InvalidoError`.

    Confere: `dmn` de cada arquivo de casos = caminho de um DMN da prova; `decisao` existe nesse DMN;
    nenhum id de decisao com o prefixo `rco_`; `casos[]` nao vazio; id de caso unico na prova; nome de
    arquivo DMN unico (a implantacao usa o nome) e id de decisao unico entre os DMN (o avaliador
    roteia so pelo id).
    """
    problemas: list[str] = []
    base = f"{BASE_PROVAS}/{prova.id}/"
    dmns: dict[str, bytes] = {}
    ids_por_dmn: dict[str, frozenset[str]] = {}
    casos_raw: list[tuple[str, bytes]] = []
    for arq in prova.arquivos:
        try:
            raw = ler_arquivo_de_prova(k1_dir, arq)
        except k1.ManifestoK1InvalidoError as exc:
            problemas.extend(exc.problemas)
            continue
        if arq.caminho.endswith(".dmn"):
            rel = arq.caminho.removeprefix(base)
            nome = rel.rsplit("/", 1)[-1]
            # `implantar_prova` envia os DMN por nome de arquivo: nome repetido sobrescreveria um deles.
            outro = next((r for r in dmns if r.rsplit("/", 1)[-1] == nome), None)
            if outro is not None:
                problemas.append(f"{arq.caminho}: nome de DMN {nome!r} repetido na prova (tambem {outro!r})")
                continue
            dmns[rel] = raw
            try:
                ids = k1.ids_de_decisao(raw, arq.caminho, exigir_prefixo=False)
            except k1.ManifestoK1InvalidoError as exc:
                problemas.extend(exc.problemas)
                continue
            # O avaliador roteia so pelo id da decisao: o mesmo id em dois DMN da prova e ambiguo.
            ja_vistos: frozenset[str] = frozenset().union(*ids_por_dmn.values())
            repetidos = sorted({i for i in ids if ids.count(i) > 1 or i in ja_vistos})
            if repetidos:
                problemas.append(f"{arq.caminho}: decisao {repetidos} repetida nos DMN da prova")
                continue
            ids_por_dmn[rel] = frozenset(ids)
            problemas.extend(
                f"{arq.caminho}: decisao {i!r} usa o prefixo reservado {k1.PREFIXO_DECISAO!r}"
                for i in ids
                if i.startswith(k1.PREFIXO_DECISAO)
            )
        elif arq.caminho.startswith(prova.esperado + "/") and arq.caminho.endswith(".json"):
            casos_raw.append((arq.caminho, raw))
    if not dmns:
        problemas.append(f"prova {prova.id}: nenhum DMN listado")
    casos: list[CasoProva] = []
    vistos: set[str] = set()
    for caminho, raw in casos_raw:
        try:
            dados = k1.json_estrito(raw)
        except ValueError as exc:
            problemas.append(f"{caminho}: ilegivel ({type(exc).__name__})")
            continue
        if not (
            isinstance(dados, dict)
            and isinstance(dados.get("dmn"), str)
            and isinstance(dados.get("decisao"), str)
            and isinstance(dados.get("casos"), list)
            and dados["casos"]
        ):
            problemas.append(f"{caminho}: faltam dmn, decisao ou casos[] nao vazio")
            continue
        if dados["dmn"] not in dmns:
            problemas.append(f"{caminho}: dmn {dados['dmn']!r} nao e um DMN listado da prova")
            continue
        if dados["decisao"] not in ids_por_dmn.get(dados["dmn"], frozenset()):
            problemas.append(f"{caminho}: decisao {dados['decisao']!r} ausente do DMN {dados['dmn']!r}")
            continue
        for i, caso in enumerate(dados["casos"]):
            entradas = caso.get("entradas") if isinstance(caso, dict) else None
            esperado = caso.get("esperado") if isinstance(caso, dict) else None
            resultado = esperado.get("resultado") if isinstance(esperado, dict) else None
            if (
                not isinstance(entradas, dict)
                or not isinstance(resultado, str)
                or resultado not in RESULTADOS
            ):
                problemas.append(f"{caminho}: casos[{i}] sem entradas ou esperado.resultado valido")
                continue
            id_caso = str(caso.get("id", i))
            if id_caso in vistos:
                problemas.append(f"{caminho}: caso {id_caso!r} repetido na prova")
                continue
            vistos.add(id_caso)
            casos.append(
                CasoProva(prova.id, caminho, dados["dmn"], dados["decisao"], id_caso, entradas, resultado)
            )
    if not casos and not problemas:
        problemas.append(f"prova {prova.id}: nenhum caso em {prova.esperado}")
    if problemas:
        raise k1.ManifestoK1InvalidoError(problemas)
    todos: frozenset[str] = frozenset().union(*ids_por_dmn.values())
    return ProvaPreparada(prova, dmns, todos, tuple(casos))


def carregar_casos(prova: k1.ProvaK1, *, k1_dir: Path) -> list[CasoProva]:
    """Casos da prova (ja conferidos por `preparar_prova`)."""
    return list(preparar_prova(prova, k1_dir=k1_dir).casos)


def resultado_da_avaliacao(linhas: Sequence[Mapping[str, Any]]) -> str:
    """`casa`/`nao_casa` da linha unica devolvida; qualquer outra forma devolve o motivo (divergente)."""
    if len(linhas) != 1:
        return f"{len(linhas)} linhas"
    valor = linhas[0].get(SAIDA_RESULTADO)
    return valor if isinstance(valor, str) and valor in RESULTADOS else f"{SAIDA_RESULTADO}={valor!r}"


async def executar_prova(prova: k1.ProvaK1, *, k1_dir: Path, avaliador: Avaliador) -> list[Divergencia]:
    """Roda todos os casos da prova; devolve as divergencias (vazia = a prova passou)."""
    divergencias: list[Divergencia] = []
    for caso in preparar_prova(prova, k1_dir=k1_dir).casos:
        onde = f"{caso.arquivo}#{caso.id_caso}"
        try:
            obtido = resultado_da_avaliacao(await avaliador.avaliar(caso.decisao, caso.entradas))
        except Exception as exc:  # o motor pode falhar de varias formas; todas recusam o pin
            obtido = f"erro: {type(exc).__name__}"
        if obtido != caso.esperado:
            divergencias.append(Divergencia(caso.prova, onde, caso.esperado, obtido))
    return divergencias


async def executar_provas(
    provas: Sequence[k1.ProvaK1], *, k1_dir: Path, avaliador: Avaliador
) -> list[Divergencia]:
    """Todas as provas do manifesto. Lista vazia = o pin pode seguir (contrato §11 item 12)."""
    divergencias: list[Divergencia] = []
    for prova in provas:
        divergencias.extend(await executar_prova(prova, k1_dir=k1_dir, avaliador=avaliador))
    return divergencias


class AvaliadorCibSeven:
    """`Avaliador` sobre o motor real, preso as definicoes DESTA implantacao (nunca "ultima por chave").

    `definicoes`: id de decisao -> id da decision-definition devolvido por `implantar_prova`. As entradas
    seguem o MESMO mapeamento de variaveis do caminho de producao (`CibSevenDmnTransport.evaluate` ->
    `dmn_transport._to_camunda_vars`): entrada nula vai como `{"value": null, "type": "String"}`.
    """

    def __init__(self, client: httpx.AsyncClient, definicoes: Mapping[str, str]) -> None:
        self._client = client
        self._definicoes = dict(definicoes)

    async def avaliar(self, decisao: str, entradas: Mapping[str, Any]) -> list[dict[str, Any]]:
        definicao = self._definicoes[decisao]
        resp = await self._client.post(
            f"/decision-definition/{definicao}/evaluate",
            json={"variables": _to_camunda_vars(dict(entradas))},
        )
        resp.raise_for_status()
        return [
            {k: _from_camunda_var(v) if isinstance(v, dict) else v for k, v in row.items()}
            for row in resp.json()
        ]


def chaves_de_decisao_do_repo(spec_dmn_dir: Path) -> frozenset[str]:
    """Ids `<decision>` de todo DMN sob `spec/processes/dmn/` (qualquer decisao fixada no repo)."""
    chaves: set[str] = set()
    for caminho in sorted(spec_dmn_dir.rglob("*.dmn")):
        chaves.update(k1.ids_de_decisao(caminho.read_bytes(), str(caminho), exigir_prefixo=False))
    return frozenset(chaves)


async def implantar_prova(
    client: httpx.AsyncClient, prova: k1.ProvaK1, *, k1_dir: Path, chaves_do_repo: frozenset[str]
) -> dict[str, str]:
    """Implanta os DMN da prova (bytes ja conferidos) e devolve `{id da decisao: id da definicao}`.

    Recusa decisao com prefixo `rco_` ou cujo id ja e decisao do repo (`chaves_do_repo`). A implantacao e
    sempre nova (sem filtro de duplicata): o id devolvido pertence a ESTA implantacao.
    """
    preparada = preparar_prova(prova, k1_dir=k1_dir)
    colisao = sorted(preparada.decisoes & chaves_do_repo)
    if colisao:
        raise k1.ManifestoK1InvalidoError([f"prova {prova.id}: decisao {colisao} colide com o repo"])
    arquivos = {
        rel.rsplit("/", 1)[-1]: (rel.rsplit("/", 1)[-1], raw, "text/xml")
        for rel, raw in preparada.dmns.items()
    }
    resp = await client.post(
        "/deployment/create", data={"deployment-name": f"rco-prova-{prova.id}"}, files=arquivos
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"implantacao da prova {prova.id} recusada: {resp.status_code}")
    definicoes = {d["key"]: str(d["id"]) for d in resp.json().get("deployedDecisionDefinitions", {}).values()}
    faltam = sorted(preparada.decisoes - set(definicoes))
    if faltam:
        raise RuntimeError(f"prova {prova.id}: o motor nao devolveu a definicao de {faltam}")
    return definicoes


class Motor(Protocol):
    """Implanta os DMN de uma prova e devolve o `Avaliador` preso a ESSA implantacao."""

    async def implantar(
        self, prova: k1.ProvaK1, *, k1_dir: Path, chaves_do_repo: frozenset[str]
    ) -> Avaliador: ...


class MotorCibSeven:
    """`Motor` sobre o CIB Seven: `implantar_prova` + `AvaliadorCibSeven` (definicoes desta implantacao)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def implantar(
        self, prova: k1.ProvaK1, *, k1_dir: Path, chaves_do_repo: frozenset[str]
    ) -> AvaliadorCibSeven:
        definicoes = await implantar_prova(self._client, prova, k1_dir=k1_dir, chaves_do_repo=chaves_do_repo)
        return AvaliadorCibSeven(self._client, definicoes)


ESTADO_SEM_PACOTE: Final[str] = rco_pin.ESTADO_SEM_PACOTE
ESTADO_FIXADO: Final[str] = rco_pin.ESTADO_FIXADO


@dataclass(frozen=True, slots=True)
class ProvasDoPin:
    """Resultado de `executar_provas_do_pin`: o estado do pin, as provas rodadas e as divergencias."""

    estado: str  # `sem_pacote` | `fixado`
    provas: tuple[str, ...]  # ids das provas executadas (vazio so em `sem_pacote`)
    casos: int  # casos avaliados no motor (soma de todas as provas)
    divergencias: tuple[Divergencia, ...]


def manifesto_do_pin(lock_path: Path, *, k1_dir: Path) -> tuple[str, k1.ManifestoK1 | None]:
    """`(estado, manifesto)` do pin. `sem_pacote` -> `None`; `fixado` -> o manifesto com o sha256 de
    `manifest_pin` e com provas (`provas_ausentes_no_pin`). Qualquer outro caso levanta
    `ManifestoK1InvalidoError` (fail-closed: nunca devolve `fixado` sem provas)."""
    try:
        lock = k1.json_estrito(lock_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise k1.ManifestoK1InvalidoError([f"pin ilegivel em {lock_path} ({type(exc).__name__})"]) from exc
    if not isinstance(lock, dict):
        raise k1.ManifestoK1InvalidoError(["pin: raiz nao e objeto"])
    try:
        # As MESMAS checagens do runtime (`rco_pin.load_rco_rules_pin`): lock_format e manifest_pin.
        rco_pin.checar_lock_format(lock)
        estado = lock.get("estado")
        if estado == ESTADO_SEM_PACOTE:
            if lock.get("manifest_pin") is not None:
                raise k1.ManifestoK1InvalidoError(["pin: estado 'sem_pacote' exige manifest_pin = null"])
            return estado, None
        if estado != ESTADO_FIXADO:
            raise k1.ManifestoK1InvalidoError([f"pin: estado desconhecido {estado!r}"])
        digest = rco_pin.checar_manifest_pin(lock)
    except rco_pin.RcoRulesPinError as exc:
        raise k1.ManifestoK1InvalidoError([exc.message]) from exc
    try:
        raw = (k1_dir / k1.K1_MANIFESTO_NOME).read_bytes()
    except OSError as exc:
        raise k1.ManifestoK1InvalidoError([f"pin: manifesto K1 ausente ou ilegivel em {k1_dir}"]) from exc
    if k1.sha256_prefixado(raw) != digest:
        raise k1.ManifestoK1InvalidoError(["pin: sha256 do manifesto K1 diverge de manifest_pin"])
    manifesto = k1.ler_manifesto(raw)
    sem_provas = k1.provas_ausentes_no_pin(manifesto)
    if sem_provas:
        raise k1.ManifestoK1InvalidoError(sem_provas)
    return estado, manifesto


async def executar_provas_do_pin(
    lock_path: Path, *, k1_dir: Path, motor: Motor, chaves_do_repo: frozenset[str]
) -> ProvasDoPin:
    """Roda TODAS as `provas[]` do pacote pinado no `motor` (contrato-k1.md §11 item 12).

    Ponto de entrada do teste de integracao que le o pin (`tests/integration/dmn/
    test_rco_provas_engine.py`): o pin real so vale com `divergencias` vazia. `sem_pacote` devolve
    `estado='sem_pacote'` e nada roda (quem chama afirma esse estado). Decisao de prova que colide com
    uma decisao do repo (`chaves_do_repo`) e recusada ANTES do motor; erro de implantacao propaga.
    """
    estado, manifesto = manifesto_do_pin(lock_path, k1_dir=k1_dir)
    if manifesto is None:
        return ProvasDoPin(estado, (), 0, ())
    divergencias: list[Divergencia] = []
    casos = 0
    for prova in manifesto.provas:
        preparada = preparar_prova(prova, k1_dir=k1_dir)
        colisao = sorted(preparada.decisoes & chaves_do_repo)
        if colisao:
            raise k1.ManifestoK1InvalidoError([f"prova {prova.id}: decisao {colisao} colide com o repo"])
        avaliador = await motor.implantar(prova, k1_dir=k1_dir, chaves_do_repo=chaves_do_repo)
        divergencias.extend(await executar_prova(prova, k1_dir=k1_dir, avaliador=avaliador))
        casos += len(preparada.casos)
    return ProvasDoPin(estado, tuple(p.id for p in manifesto.provas), casos, tuple(divergencias))


__all__ = [
    "RESULTADOS",
    "Avaliador",
    "AvaliadorCibSeven",
    "ProvaPreparada",
    "chaves_de_decisao_do_repo",
    "ler_arquivo_de_prova",
    "preparar_prova",
    "CasoProva",
    "Divergencia",
    "carregar_casos",
    "executar_prova",
    "executar_provas",
    "implantar_prova",
    "resultado_da_avaliacao",
    "Motor",
    "MotorCibSeven",
    "ProvasDoPin",
    "executar_provas_do_pin",
    "manifesto_do_pin",
]
