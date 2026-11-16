#!/usr/bin/env python3
"""Gate de CI: o pin do pacote de regras K1 do rco esta intacto e completo (ADR-0075 secao b).

Proposito
---------
O ADR-0075 faz do repositorio `regras-contratos-operadora` (rco) o unico dono das tabelas DMN de
"o que" (cobertura, DUT, carencia, autorizacao, limites, pacotes, OPME, glosa). O Maezo recebe o
pacote K1 byte a byte em `spec/processes/dmn/rco/` e guarda um pin imutavel,
`config/integrations/rco/rules.lock.json`, no formato de `config/integrations/amh/contracts.lock.json`.
Sem este gate o pin seria JSON solto: um digest editado, um `.dmn` a mais jogado na pasta ou um
arquivo do pacote apagado passariam verdes.

Desenho
-------
Espelho de `scripts/ci/verify_amh_contract_pin.py`: nucleo puro (`load_lock` / `verify_lock`) que
devolve violacoes em vez de sair, mais um CLI fino (`main`). Somente biblioteca padrao
(`scripts/ci/__init__.py`) e SEM rede: verifica bytes que ja estao na arvore. Fail-closed e sem
mascarar: todas as checagens rodam mesmo depois de uma falha, e a saida e 0 so com zero violacoes.

Digest
------
Todo `sha256` do pin esta na notacao do pacote, `sha256:<64 hex minusculos>` (contrato-k1.md §1 e
§11 item 1; mesma notacao do K2, HEL-3a): o hex cru dispara o detector de segredos e e recusado.

Estados do pin (`estado`)
-------------------------
- `sem_pacote`: o rco ainda nao publicou o K1. `provenance.rco_commit_sha` e null, `manifest_pin`
  e null e `artifacts` e `[]`. A pasta `spec/processes/dmn/rco/` pode estar ausente ou conter so
  `README.md` e os arquivos listados em `exemplos_sinteticos` (path + sha256), e
  `config/integrations/rco/k1/` tem de estar ausente ou vazia. Qualquer outro arquivo reprova.
- `fixado`: ha pacote. Exige `provenance.rco_repo` e `provenance.rco_commit_sha` (40 hex), iguais a
  `origem` do manifesto; `manifest_pin` com `path` = `config/integrations/rco/k1/manifesto.json` e
  `sha256` (o conteudo de `manifesto.sha256` do pacote); e `artifacts` nao vazio, igual a
  `decisoes[]` do manifesto (`caminho_consumidor` + `sha256`). O manifesto e lido por
  `src/maezo/platform/rco_manifesto.py` — o mesmo leitor do runtime, carregado aqui PELO CAMINHO do
  arquivo (sem `import maezo`, entao o gate segue so biblioteca padrao): formato MAJOR 1 ou 2 (2.0.0:
  `ativacao`/`exige_aditivo` por regra e bloco `ativacao` de `vocabulario.json`; regra fora de
  `plena` sem aditivo e recusada ate a Onda 2 item 5), sem `origem.sujo`, campos exatos, prefixo
  `rco_`, anexos (`vocabulario.json`, `entradas.json`, `k6-filas.json`) com o sha256 do manifesto,
  colunas de saida dentro do `saidas[]` de cada decisao, `idRegra` emitidos = `regras[]` e linha
  adversa com `idRegra`/`fila`/`classeAdverso`/`motivoTab38` preenchidos.
  Em `config/integrations/rco/k1/` so entram o manifesto e esses anexos (os exemplos do pacote ficam
  fora do lock e fora da arvore: contrato §3). No formato 2.1 o manifesto lista tambem `provas[]`:
  cada arquivo de prova fica em `k1/provas/<id>/...` com o sha256 do manifesto, e arquivo em `provas/`
  fora do manifesto reprova (contrato §4 item 7); as provas NAO entram em `artifacts` (fora do lock).
  Cada `rco/*.dmn` tem de estar listado com sha256
  igual; nada fora da lista e aceito.

Em ambos os estados: subpasta em `rco/` ou em `k1/`, link simbolico, caminho absoluto ou com `..`,
entrada duplicada, arquivo listado ausente e exemplo sintetico com id de decisao sem `rco_` ou
repetido reprovam (a varredura das pastas e `rco_manifesto.verificar_pastas`, a mesma do runtime).

Codigos de saida (mesmos do verificador AMH): 0 = passa; 1 = falha (pin ou arvore); 2 = uso
invalido da linha de comando (argparse).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = "config/integrations/rco/rules.lock.json"
RULES_DIR = "spec/processes/dmn/rco"
#: Pasta plana do manifesto K1 e dos anexos (= `maezo.platform.rco_manifesto.K1_DIR`).
K1_DIR = "config/integrations/rco/k1"
K1_MANIFEST_PATH = f"{K1_DIR}/manifesto.json"
#: Leitor unico do manifesto (gate e runtime), relativo a raiz DESTE repositorio.
K1_READER = "src/maezo/platform/rco_manifesto.py"
LOCK_NAME = "rco-rules-lock"
#: Versao do formato do lock: 2.0.0 trouxe o digest `sha256:<hex>` (contrato-k1.md §11 item 1).
LOCK_VERSION = "2.0.0"
README_NAME = "README.md"

ESTADO_SEM_PACOTE = "sem_pacote"
ESTADO_FIXADO = "fixado"
ESTADOS = (ESTADO_SEM_PACOTE, ESTADO_FIXADO)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Violation:
    """Um motivo fail-closed para nao confiar no pin."""

    code: str
    detail: str

    def render(self) -> str:
        return f"[{self.code}] {self.detail}"


@dataclass(frozen=True)
class GateResult:
    """Resultado da verificacao. Dado puro; nunca sai do processo."""

    ok: bool
    violations: tuple[Violation, ...] = ()
    checks: tuple[str, ...] = field(default=())

    def render(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [f"[rco-rules-pin:lock] {status}"]
        lines.extend(f"  . {check}" for check in self.checks)
        if self.violations:
            lines.append(f"  violacoes ({len(self.violations)}):")
            lines.extend(f"    - {v.render()}" for v in self.violations)
        return "\n".join(lines)


def sha256_of(path: Path) -> str:
    """Digest do arquivo na notacao do pin: `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class _DuplicateKeyError(ValueError):
    """Chave repetida no mesmo objeto JSON (json.loads ficaria com a ultima, em silencio)."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKeyError(key)
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"constante JSON nao padrao {name!r} (NaN/Infinity) recusada")


def _unsafe_path(repo_root: Path, rel: PurePosixPath) -> str | None:
    """Motivo para recusar `rel` (link simbolico em qualquer componente ou fuga da raiz), ou None."""
    current = repo_root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return f"{current.relative_to(repo_root).as_posix()} e link simbolico; o pin exige caminho real"
    if not current.resolve().is_relative_to(repo_root.resolve()):
        return f"{rel} resolve para fora da raiz do repositorio"
    return None


def load_lock(path: Path) -> tuple[dict[str, Any] | None, list[Violation]]:
    """Le e interpreta o pin. Devolve `(lock_ou_None, violacoes)`; nao levanta excecao."""
    if not path.is_file():
        return None, [Violation("lock-ausente", f"pin nao encontrado: {path}")]
    try:
        parsed = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_no_constant,
        )
    except _DuplicateKeyError as exc:
        return None, [Violation("lock-chave-duplicada", f"{path}: chave JSON repetida {exc.args[0]!r}")]
    except (OSError, UnicodeDecodeError) as exc:
        return None, [Violation("lock-ilegivel", f"{path}: {exc}")]
    except ValueError as exc:  # JSONDecodeError e NaN/Infinity
        return None, [Violation("lock-json-invalido", f"{path}: JSON invalido ({exc})")]
    if not isinstance(parsed, dict):
        return None, [Violation("lock-nao-objeto", f"{path}: o topo tem de ser um objeto JSON")]
    return parsed, []


def _safe_relpath(raw: object, where: str, violations: list[Violation]) -> PurePosixPath | None:
    if not isinstance(raw, str) or not raw:
        violations.append(Violation("caminho-invalido", f"{where}: 'path' tem de ser texto nao vazio"))
        return None
    rel = PurePosixPath(raw)
    if "\\" in raw or rel.is_absolute() or ".." in rel.parts or str(rel) != raw:
        violations.append(
            Violation(
                "caminho-invalido", f"{where}: caminho {raw!r} deve ser relativo, normalizado, sem '..'"
            )
        )
        return None
    return rel


def _check_entries(entries: object, section: str, violations: list[Violation]) -> dict[PurePosixPath, str]:
    """Valida uma lista de `{path, sha256}` dentro de `rco/`; devolve {caminho: sha256}."""
    out: dict[PurePosixPath, str] = {}
    if not isinstance(entries, list):
        violations.append(Violation("secao-tipo", f"'{section}' tem de ser uma lista"))
        return out
    rules_dir = PurePosixPath(RULES_DIR)
    for idx, entry in enumerate(entries):
        where = f"{section}[{idx}]"
        if not isinstance(entry, dict):
            violations.append(Violation("entrada-tipo", f"{where}: tem de ser um objeto com path e sha256"))
            continue
        rel = _safe_relpath(entry.get("path"), where, violations)
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _SHA256.match(digest):
            violations.append(
                Violation("sha256-invalido", f"{where}: 'sha256' tem de ser sha256:<64 hex minusculos>")
            )
            digest = None
        if rel is None:
            continue
        if rel.parent != rules_dir or rel.suffix != ".dmn":
            violations.append(
                Violation(
                    "caminho-fora-da-pasta",
                    f"{where}: {rel} tem de ser um .dmn direto em {RULES_DIR}/ (sem subpasta)",
                )
            )
            continue
        if rel in out:
            violations.append(Violation("entrada-duplicada", f"{where}: {rel} listado mais de uma vez"))
            continue
        out[rel] = digest or ""
    return out


def _check_digest(repo_root: Path, rel: PurePosixPath, expected: str, label: str) -> list[Violation]:
    path = repo_root / rel
    unsafe = _unsafe_path(repo_root, rel)
    if unsafe is not None:
        return [Violation("link-simbolico", f"{label}: {unsafe}")]
    if not path.is_file():
        return [Violation("arquivo-listado-ausente", f"{label} {rel} esta no pin mas nao existe na arvore")]
    if not expected:
        return []  # sha256 invalido ja reportado
    actual = sha256_of(path)
    if actual != expected:
        return [
            Violation(
                "sha256-divergente",
                f"{label} {rel}: sha256 recalculado {actual} != pinado {expected}",
            )
        ]
    return []


def verify_lock(lock: dict[str, Any], *, repo_root: Path) -> list[Violation]:
    """Todas as checagens do pin contra a arvore. Devolve a lista completa de violacoes."""
    violations: list[Violation] = []

    fmt = lock.get("lock_format")
    if not isinstance(fmt, dict):
        violations.append(Violation("secao-ausente", "'lock_format' ausente ou nao e objeto"))
    else:
        if fmt.get("name") != LOCK_NAME:
            violations.append(Violation("lock-nome", f"lock_format.name tem de ser {LOCK_NAME!r}"))
        if fmt.get("rules_dir") != RULES_DIR:
            violations.append(Violation("lock-pasta", f"lock_format.rules_dir tem de ser {RULES_DIR!r}"))
        if fmt.get("version") != LOCK_VERSION:
            violations.append(
                Violation(
                    "lock-versao", f"lock_format.version tem de ser {LOCK_VERSION!r} (digest sha256:<hex>)"
                )
            )

    estado = lock.get("estado")
    if estado not in ESTADOS:
        violations.append(
            Violation("estado-invalido", f"'estado' deve ser um de {list(ESTADOS)}: {estado!r}")
        )

    provenance = lock.get("provenance")
    if not isinstance(provenance, dict):
        violations.append(Violation("secao-ausente", "'provenance' ausente ou nao e objeto"))
        provenance = {}

    for section in ("manifest_pin", "artifacts", "exemplos_sinteticos"):
        if section not in lock:
            violations.append(Violation("secao-ausente", f"secao obrigatoria ausente: {section!r}"))

    artifacts = _check_entries(lock.get("artifacts", []), "artifacts", violations)
    exemplos = _check_entries(lock.get("exemplos_sinteticos", []), "exemplos_sinteticos", violations)
    for rel in sorted(set(artifacts) & set(exemplos)):
        violations.append(
            Violation("entrada-duplicada", f"{rel} esta em artifacts e em exemplos_sinteticos ao mesmo tempo")
        )

    manifest_ok = False
    manifest = lock.get("manifest_pin")
    if estado == ESTADO_SEM_PACOTE:
        if provenance.get("rco_commit_sha") is not None:
            violations.append(
                Violation("estado-incoerente", "estado 'sem_pacote' exige provenance.rco_commit_sha = null")
            )
        if manifest is not None:
            violations.append(Violation("estado-incoerente", "estado 'sem_pacote' exige manifest_pin = null"))
        if artifacts or lock.get("artifacts"):
            violations.append(Violation("estado-incoerente", "estado 'sem_pacote' exige artifacts = []"))
    elif estado == ESTADO_FIXADO:
        repo = provenance.get("rco_repo")
        if not isinstance(repo, str) or not repo:
            violations.append(Violation("proveniencia", "estado 'fixado' exige provenance.rco_repo"))
        commit = provenance.get("rco_commit_sha")
        if not isinstance(commit, str) or not _COMMIT.match(commit):
            violations.append(
                Violation("proveniencia", "estado 'fixado' exige provenance.rco_commit_sha (40 hex)")
            )
        if not artifacts:
            violations.append(Violation("pacote-vazio", "estado 'fixado' exige artifacts nao vazio"))
        if not isinstance(manifest, dict):
            violations.append(Violation("manifesto", "estado 'fixado' exige manifest_pin com path e sha256"))
        else:
            manifest_rel = _safe_relpath(manifest.get("path"), "manifest_pin", violations)
            digest = manifest.get("sha256")
            if not isinstance(digest, str) or not _SHA256.match(digest):
                violations.append(
                    Violation("sha256-invalido", "manifest_pin: 'sha256' exige sha256:<64 hex minusculos>")
                )
                digest = ""
            if set(manifest) != {"path", "sha256"}:
                violations.append(
                    Violation(
                        "manifesto", f"manifest_pin tem de ter exatamente path e sha256: {sorted(manifest)}"
                    )
                )
            if manifest_rel is not None and str(manifest_rel) != K1_MANIFEST_PATH:
                violations.append(
                    Violation(
                        "manifesto-caminho",
                        f"manifest_pin.path tem de ser {K1_MANIFEST_PATH!r}: {manifest_rel}",
                    )
                )
                manifest_rel = None
            if manifest_rel is not None:
                found = _check_digest(repo_root, manifest_rel, str(digest), "manifesto")
                violations.extend(found)
                manifest_ok = not found and bool(digest)
        if manifest_ok:
            violations.extend(_check_package(repo_root, provenance, artifacts))

    for rel, digest in sorted(artifacts.items()):
        violations.extend(_check_digest(repo_root, rel, digest, "artefato"))
    for rel, digest in sorted(exemplos.items()):
        violations.extend(_check_digest(repo_root, rel, digest, "exemplo sintetico"))

    violations.extend(_check_decision_ids(repo_root, {**artifacts, **exemplos}))
    violations.extend(_scan_dirs(repo_root, set(artifacts) | set(exemplos), estado == ESTADO_FIXADO))
    return violations


_K1_MODULE_NAME = "_verify_rco_rules_pin_rco_manifesto"


def _k1() -> ModuleType:
    """O leitor `src/maezo/platform/rco_manifesto.py`, carregado PELO CAMINHO do arquivo.

    E o mesmo leitor do runtime (um formato so), mas sem `import maezo...`: isso rodaria
    `maezo/platform/__init__.py` (structlog etc.) e o gate deixaria de ser so biblioteca padrao. O
    modulo nao importa nada de `maezo`.
    """
    cached = sys.modules.get(_K1_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_K1_MODULE_NAME, REPO_ROOT / K1_READER)
    if spec is None or spec.loader is None:  # pragma: no cover - arquivo do proprio repo
        raise RuntimeError(f"leitor do manifesto K1 nao carregavel: {K1_READER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_K1_MODULE_NAME] = module  # dataclasses resolve o modulo por nome
    spec.loader.exec_module(module)
    return module


def _check_package(
    repo_root: Path, provenance: dict[str, Any], artifacts: dict[PurePosixPath, str]
) -> list[Violation]:
    """Le o manifesto pinado com o leitor do runtime e cruza com o pin (so com manifesto integro)."""
    k1 = _k1()
    try:
        manifesto = k1.ler_manifesto((repo_root / K1_MANIFEST_PATH).read_bytes())
    except k1.ManifestoK1InvalidoError as exc:
        return [Violation("manifesto-invalido", f"{K1_MANIFEST_PATH}: {p}") for p in exc.problemas]
    violations: list[Violation] = []
    if (
        provenance.get("rco_repo") != manifesto.repo
        or provenance.get("rco_commit_sha") != manifesto.commit_sha
    ):
        violations.append(
            Violation(
                "proveniencia",
                "provenance.rco_repo/rco_commit_sha tem de ser a origem do manifesto "
                f"({manifesto.repo} @ {manifesto.commit_sha})",
            )
        )
    esperado = {PurePosixPath(d.caminho_consumidor): d.sha256 for d in manifesto.decisoes.values()}
    for rel in sorted(set(esperado) - set(artifacts)):
        violations.append(Violation("artefato-fora-do-pin", f"{rel} esta no manifesto mas nao em artifacts"))
    for rel in sorted(set(artifacts) - set(esperado)):
        violations.append(
            Violation("artefato-fora-do-manifesto", f"{rel} esta em artifacts mas nao no manifesto")
        )
    for rel in sorted(set(artifacts) & set(esperado)):
        if artifacts[rel] and artifacts[rel] != esperado[rel]:
            violations.append(
                Violation(
                    "sha256-divergente", f"artifacts {rel}: sha256 do pin != decisoes[].sha256 do manifesto"
                )
            )
    problemas = k1.verificar_pacote(manifesto, k1_dir=repo_root / K1_DIR, rules_dir=repo_root / RULES_DIR)
    violations.extend(Violation("pacote-k1", problema) for problema in problemas)
    # Mesma recusa do runtime: ativacao sem significado ligado (Onda 2 item 5).
    violations.extend(Violation("ativacao-nao-suportada", p) for p in k1.ativacao_nao_suportada(manifesto))
    return violations


def _check_decision_ids(repo_root: Path, listed: dict[PurePosixPath, str]) -> list[Violation]:
    """Ids de decisao de todo `.dmn` listado (artefatos e exemplos sinteticos do Maezo), em qualquer
    estado: prefixo `rco_` e nenhum id em dois arquivos — a mesma leitura do runtime."""
    if not listed:
        return []
    k1 = _k1()
    violations: list[Violation] = []
    vistos: dict[str, PurePosixPath] = {}
    for rel in sorted(listed):
        path = repo_root / rel
        if path.is_symlink() or not path.is_file():
            continue  # ausencia/link ja reportados por _check_digest
        try:
            ids = k1.ids_de_decisao(path.read_bytes(), str(rel))
        except k1.ManifestoK1InvalidoError as exc:
            violations.extend(Violation("decisao-invalida", p) for p in exc.problemas)
            continue
        for decision_id in ids:
            if decision_id in vistos:
                violations.append(
                    Violation(
                        "decisao-duplicada", f"{rel}: decisao {decision_id!r} tambem em {vistos[decision_id]}"
                    )
                )
            vistos[decision_id] = rel
    return violations


def _scan_dirs(repo_root: Path, listed: set[PurePosixPath], fixado: bool) -> list[Violation]:
    """`rco/` e `k1/` so com o que pin e manifesto declaram: `rco_manifesto.verificar_pastas`, a MESMA
    varredura do runtime, mais a recusa de link simbolico nos ancestrais (so faz sentido no checkout)."""
    violations: list[Violation] = []
    for rel_dir in (RULES_DIR, K1_DIR):
        unsafe = _unsafe_path(repo_root, PurePosixPath(rel_dir))
        if unsafe is not None:
            violations.append(Violation("link-simbolico", f"pasta {rel_dir}: {unsafe}"))
    if violations:
        return violations
    k1 = _k1()
    nomes_k1: set[str] = set()
    if fixado:
        manifesto = repo_root / K1_MANIFEST_PATH
        raw = manifesto.read_bytes() if manifesto.is_file() and not manifesto.is_symlink() else None
        nomes_k1 = k1.nomes_k1_declarados(raw)
    achados = k1.verificar_pastas(
        rules_dir=repo_root / RULES_DIR,
        k1_dir=repo_root / K1_DIR,
        listados_rco={rel.name for rel in listed},
        nomes_k1=nomes_k1,
    )
    return [Violation(code, detail) for code, detail in achados]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_rco_rules_pin",
        description=(
            "Gate de CI (ADR-0075 b): verifica o pin do pacote de regras K1 do rco. Fail-closed: "
            "saida 0 so quando todas as checagens passam; todas as violacoes saem numa so execucao. "
            "Sem rede."
        ),
    )
    parser.add_argument("--lock", default=DEFAULT_LOCK_PATH, help=f"Pin (padrao: {DEFAULT_LOCK_PATH}).")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Raiz do repositorio.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada. Devolve 0 (passa) ou 1 (falha); argparse sai com 2 em uso invalido."""
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    lock_path = Path(args.lock)
    if not lock_path.is_absolute():
        lock_path = repo_root / lock_path

    lock, violations = load_lock(lock_path)
    if lock is None:
        print(GateResult(ok=False, violations=tuple(violations)).render(), file=sys.stderr)
        return 1

    found = verify_lock(lock, repo_root=repo_root)
    artifacts = lock.get("artifacts")
    exemplos = lock.get("exemplos_sinteticos")
    rel_lock = lock_path.relative_to(repo_root) if lock_path.is_relative_to(repo_root) else lock_path
    result = GateResult(
        ok=not found,
        violations=tuple(found),
        checks=(
            f"pin: {rel_lock} (estado={lock.get('estado')!r})",
            f"{len(artifacts) if isinstance(artifacts, list) else 0} artefato(s) K1 e "
            f"{len(exemplos) if isinstance(exemplos, list) else 0} exemplo(s) sintetico(s) re-hasheados",
            f"{RULES_DIR}/: nada fora do pin alem de {README_NAME}; {K1_DIR}/: so manifesto e anexos pinados",
        ),
    )
    print(result.render(), file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
