"""Tabelas do pacote K1 pinado fora do corpus da cerca de perspectiva (adendo ao ADR-0040, Proposed).

O QUE E. A cerca de perspectiva (`perspective.py`, ADR-0040 D7) le o texto das especificacoes ESCRITAS PELO
MAEZO. As tabelas do pacote K1 (`spec/processes/dmn/rco/`) nao sao escritas aqui: sao bytes do rco, copiados
byte a byte e fixados pelo pin (`config/integrations/rco/rules.lock.json`, ADR-0075 (b)). Este modulo decide,
por PASTA e pelo PIN, quais arquivos de `rco/` sao esses bytes: `tabelas_k1_pinadas` devolve exatamente os
`artifacts` de um pin `fixado` que confere inteiro com o manifesto que ele pina. `make validate-artifacts`
(`cli.py`) tira esses arquivos do corpus da cerca; tudo o mais continua lido, com o mesmo vocabulario.

O QUE NAO E. Nao e excecao por arquivo, nem marcador no texto, nem lista editavel: o modulo nao le arquivo
de configuracao proprio, so o pin e o manifesto que o ADR-0075 ja fixa. A cerca (`perspective.py`) continua
sem mecanismo de excecao e le todo arquivo que receber. Arquivo fora de `rco/` nunca sai do corpus (o
conjunto e montado so com `dmn_dir/rco/<nome>`), e nunca sai o exemplo sintetico do Maezo
(`exemplos_sinteticos`), que e texto escrito aqui.

FAIL-CLOSED: qualquer divergencia devolve o conjunto VAZIO, ou seja, a cerca le `rco/` inteira como hoje.
Conjunto vazio quando: o pin nao e `fixado` (`sem_pacote`, o estado comitado), esta ilegivel ou fora do
formato; `manifest_pin` falta ou diverge do sha256 do manifesto; o manifesto e recusado pelo leitor
(`rco_manifesto.ler_manifesto`), nao tem provas do motor, tem ativacao fora do contrato ou `origem` diferente
de `provenance`; `artifacts` nao e exatamente `decisoes[]` (caminho e sha256); um arquivo listado (artefato
ou exemplo sintetico) falta, e link simbolico ou tem outro sha256; `rco/` ou `k1/` tem arquivo fora do pin
(`rco_manifesto.verificar_pastas`, a mesma varredura do gate e do runtime); a pasta lida nao e a `rco/` do
repositorio do pin. As checagens sao as do gate `scripts/ci/verify_rco_rules_pin.py`, pelo mesmo leitor
(`maezo.platform.rco_manifesto`); a prova de que gate e isencao concordam fica nos testes.

Com o pin `sem_pacote` o conjunto e vazio e o comportamento e o de antes deste modulo. A isencao so passa a
valer quando um PR pinar tabelas reais (estado `fixado`), e so para os bytes com o sha256 do manifesto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from maezo.platform import rco_manifesto

from .dmn import RCO_SUBDIR

#: O pin, relativo a raiz do repositorio (= `rco_pin.RCO_RULES_PIN_RELATIVE_PATH` e o padrao do gate).
LOCK_RELPATH: Final[Path] = Path("config") / "integrations" / "rco" / "rules.lock.json"
LOCK_NAME: Final[str] = "rco-rules-lock"
LOCK_VERSION: Final[str] = "2.0.0"
ESTADO_FIXADO: Final[str] = "fixado"


@dataclass(frozen=True, slots=True)
class TabelasK1Pinadas:
    """Os arquivos de `rco/` que sao bytes do pacote pinado, e por que o conjunto e o que e."""

    caminhos: frozenset[Path]
    motivo: str


class _RecusaError(Exception):
    """Divergencia que zera o conjunto (mensagem so com caminhos e ids do repositorio)."""


def _entradas(lock: Mapping[str, Any], secao: str) -> dict[PurePosixPath, str]:
    bruto = lock.get(secao)
    if not isinstance(bruto, list):
        raise _RecusaError(f"{secao} ausente ou nao e lista")
    out: dict[PurePosixPath, str] = {}
    for item in bruto:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise _RecusaError(f"{secao}: entrada sem exatamente path e sha256")
        caminho, digest = item["path"], item["sha256"]
        if not isinstance(caminho, str) or not rco_manifesto.is_digest(digest):
            raise _RecusaError(f"{secao}: path ou sha256 invalido")
        rel = PurePosixPath(caminho)
        if (
            str(rel) != caminho
            or rel.parent != PurePosixPath(rco_manifesto.RULES_DIR)
            or rel.suffix != ".dmn"
        ):
            raise _RecusaError(f"{secao}: {caminho!r} nao e um .dmn direto em {rco_manifesto.RULES_DIR}")
        if rel in out:
            raise _RecusaError(f"{secao}: {caminho} repetido")
        out[rel] = str(digest)
    return out


def _bytes_listados(rco_dir: Path, listados: Mapping[PurePosixPath, str]) -> None:
    for rel, digest in listados.items():
        caminho = rco_dir / rel.name
        if caminho.is_symlink() or not caminho.is_file():
            raise _RecusaError(f"{rel}: listado no pin e ausente (ou link simbolico)")
        if rco_manifesto.sha256_prefixado(caminho.read_bytes()) != digest:
            raise _RecusaError(f"{rel}: sha256 diverge do pin")


def _verificar(dmn_dir: Path, lock_path: Path) -> frozenset[Path]:
    raiz = lock_path.parent.parent.parent.parent
    if raiz / LOCK_RELPATH != lock_path:
        raise _RecusaError(f"pin fora de {LOCK_RELPATH.as_posix()}")
    rco_dir = dmn_dir / RCO_SUBDIR
    if rco_dir.is_symlink() or rco_dir.resolve() != (raiz / rco_manifesto.RULES_DIR).resolve():
        raise _RecusaError(f"a pasta lida nao e {rco_manifesto.RULES_DIR} do repositorio do pin")
    try:
        lock = rco_manifesto.json_estrito(lock_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise _RecusaError(f"pin ilegivel ({type(exc).__name__})") from None
    if not isinstance(lock, dict):
        raise _RecusaError("pin: raiz nao e objeto")
    formato = lock.get("lock_format")
    if (
        not isinstance(formato, dict)
        or formato.get("name") != LOCK_NAME
        or formato.get("rules_dir") != rco_manifesto.RULES_DIR
        or formato.get("version") != LOCK_VERSION
    ):
        raise _RecusaError("pin: lock_format fora do formato")
    if lock.get("estado") != ESTADO_FIXADO:
        raise _RecusaError(f"pin em estado {lock.get('estado')!r}: sem tabelas do pacote")
    pin = lock.get("manifest_pin")
    if (
        not isinstance(pin, dict)
        or set(pin) != {"path", "sha256"}
        or pin.get("path") != rco_manifesto.K1_MANIFESTO_PATH
        or not rco_manifesto.is_digest(pin.get("sha256"))
    ):
        raise _RecusaError("pin: manifest_pin fora do formato")
    k1_dir = raiz / rco_manifesto.K1_DIR
    caminho_manifesto = k1_dir / rco_manifesto.K1_MANIFESTO_NOME
    if k1_dir.is_symlink() or caminho_manifesto.is_symlink() or not caminho_manifesto.is_file():
        raise _RecusaError("manifesto K1 ausente (ou link simbolico)")
    raw = caminho_manifesto.read_bytes()
    if rco_manifesto.sha256_prefixado(raw) != pin["sha256"]:
        raise _RecusaError("sha256 do manifesto K1 diverge de manifest_pin")
    try:
        manifesto = rco_manifesto.ler_manifesto(raw)
    except rco_manifesto.ManifestoK1InvalidoError:
        raise _RecusaError("manifesto K1 recusado pelo leitor") from None
    if rco_manifesto.provas_ausentes_no_pin(manifesto) or rco_manifesto.ativacao_nao_suportada(manifesto):
        raise _RecusaError("manifesto K1 sem provas do motor ou com ativacao fora do contrato")
    provenance = lock.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("rco_repo") != manifesto.repo
        or provenance.get("rco_commit_sha") != manifesto.commit_sha
    ):
        raise _RecusaError("provenance difere da origem do manifesto K1")
    artefatos = _entradas(lock, "artifacts")
    exemplos = _entradas(lock, "exemplos_sinteticos")
    esperado = {PurePosixPath(d.caminho_consumidor): d.sha256 for d in manifesto.decisoes.values()}
    if not artefatos or artefatos != esperado or set(artefatos) & set(exemplos):
        raise _RecusaError("artifacts nao e exatamente decisoes[] do manifesto K1")
    _bytes_listados(rco_dir, {**artefatos, **exemplos})
    ids: list[str] = []
    for rel in (*artefatos, *exemplos):
        raw_dmn = (rco_dir / rel.name).read_bytes()
        # prefixo `rco_` obrigatorio: sem ele, `ManifestoK1InvalidoError` (conjunto vazio)
        ids.extend(rco_manifesto.ids_de_decisao(raw_dmn, str(rel)))
        if rel in artefatos and rco_manifesto.tolerancia_prazo_conta_no_dmn(raw_dmn, str(rel)):
            raise _RecusaError(f"{rel}: tolerancia do prazo de conta no DMN (MZO-TOL), recusada pelo gate")
    if len(ids) != len(set(ids)):
        raise _RecusaError("id de decisao repetido entre os arquivos listados no pin")
    if rco_manifesto.verificar_pacote(manifesto, k1_dir=k1_dir, rules_dir=rco_dir):
        raise _RecusaError("pacote K1 nao confere com o manifesto")
    soltos = rco_manifesto.verificar_pastas(
        rules_dir=rco_dir,
        k1_dir=k1_dir,
        listados_rco={rel.name for rel in (*artefatos, *exemplos)},
        nomes_k1=rco_manifesto.nomes_k1_declarados(raw),
    )
    if soltos:
        raise _RecusaError("arquivo fora do pin em rco/ ou k1/")
    return frozenset(rco_dir / rel.name for rel in artefatos)


def tabelas_k1_pinadas(dmn_dir: Path, lock_path: Path) -> TabelasK1Pinadas:
    """Os `artifacts` do pin `fixado` que conferem com o manifesto, como `dmn_dir/rco/<nome>`; senao vazio.

    `dmn_dir` e a pasta `spec/processes/dmn` lida pela validacao; `lock_path`, o pin do mesmo repositorio
    (`<raiz>/config/integrations/rco/rules.lock.json`). Nunca levanta: divergencia = conjunto vazio.
    """
    try:
        caminhos = _verificar(dmn_dir, lock_path)
    except _RecusaError as recusa:
        return TabelasK1Pinadas(frozenset(), str(recusa))
    except (OSError, ValueError, rco_manifesto.ManifestoK1InvalidoError) as exc:
        return TabelasK1Pinadas(frozenset(), f"pin nao verificavel ({type(exc).__name__})")
    return TabelasK1Pinadas(caminhos, f"pin fixado: {len(caminhos)} tabela(s) do pacote K1")


__all__ = ["LOCK_RELPATH", "TabelasK1Pinadas", "tabelas_k1_pinadas"]
