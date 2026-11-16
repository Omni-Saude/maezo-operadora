#!/usr/bin/env python3
"""Gate de CI: o pin do pacote K2 (Manual do Beneficiario) do rco esta intacto e completo.

Proposito
---------
O K2 e o pacote de conteudo do Manual do Beneficiario que o rco publica junto do K1 (contrato-k2.md
do rco). O Maezo guarda o pin `config/integrations/rco/k2.lock.json` e os bytes em
`config/integrations/rco/k2/` (pasta plana). Sem este gate o pin seria JSON solto: um digest editado,
um arquivo a mais ou um arquivo do pacote apagado passariam verdes. O leitor em producao (Helena,
`manual.py`) le `versao` e `manifest_pin.sha256` deste lock; ele recusa `ratificado: false`, o gate
NAO (a ratificacao e decisao do dono, nao integridade de bytes).

Desenho
-------
Irmao de `verify_rco_rules_pin.py` (K1): nucleo puro (`load_lock` / `verify_lock`) que devolve
violacoes em vez de sair, mais um CLI fino (`main`). So biblioteca padrao e SEM rede. Reusa os
auxiliares do leitor K1 (`src/maezo/platform/rco_manifesto.py`: bytes canonicos, `json_estrito`,
notacao de digest), carregado PELO CAMINHO do arquivo, sem `import maezo`. Fail-closed e sem
mascarar: todas as checagens rodam mesmo depois de uma falha; a saida e 0 so com zero violacoes.

Estados (`estado`)
------------------
- `sem_pacote`: `provenance.rco_commit_sha`, `manifest_pin` e `versao` sao null; a pasta
  `config/integrations/rco/k2/` esta ausente ou so tem `README.md`.
- `fixado`: a pasta tem exatamente 7 arquivos: `manifesto.json`, `manifesto.sha256` e os 5 de dados.
  Contrato-k2 §2: bytes canonicos do manifesto; campos exatos `formato` (`rco-k2-v1`), `versao`,
  `commit_rco` (40 hex), `gerado_em` (`AAAA-MM-DDTHH:MM:SSZ`), `ratificado` (booleano), `arquivos`
  (os 5 arquivos de dados -> `sha256:<hex>`); `manifesto.sha256` e exatamente `sha256:<hex>` + LF e
  igual ao hash recomputado; cada arquivo existe com o digest do manifesto; nada fora do manifesto;
  o lock concorda (`manifest_pin.sha256`, `versao`, `rco_commit_sha`).
- Cruzamento com o K1: com K1 e K2 `fixado`, `versao` do K2 = `versao_pacote` do K1 e os commits
  (`commit_rco` do K2, `origem.commit_sha` do K1) sao iguais.

Link simbolico, `..`, caminho absoluto, chave JSON repetida, NaN/Infinity e hex cru reprovam.
Codigos de saida: 0 = passa; 1 = falha; 2 = uso invalido (argparse).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = "config/integrations/rco/k2.lock.json"
K1_LOCK_PATH = "config/integrations/rco/rules.lock.json"
K1_MANIFEST_PATH = "config/integrations/rco/k1/manifesto.json"
K2_DIR = "config/integrations/rco/k2"
K2_MANIFEST_NAME = "manifesto.json"
K2_MANIFEST_PATH = f"{K2_DIR}/{K2_MANIFEST_NAME}"
K2_HASH_NAME = "manifesto.sha256"
README_NAME = "README.md"
#: Os 5 arquivos de dados (contrato-k2 §1).
DATA_FILES = (
    "search-index.json",
    "capitulo-geral.yaml",
    "sinonimos.json",
    "sinonimos-por-termo.json",
    "avaliacoes.yaml",
)
PACKAGE_FILES = frozenset({K2_MANIFEST_NAME, K2_HASH_NAME, *DATA_FILES})
READER = "src/maezo/platform/rco_manifesto.py"
LOCK_NAME = "rco-k2-lock"
LOCK_VERSION = "1.0.0"
RCO_REPO = "Omni-Saude/regras-contratos-operadora"
MANIFEST_FORMAT = "rco-k2-v1"
MANIFEST_FIELDS = frozenset({"formato", "versao", "commit_rco", "gerado_em", "ratificado", "arquivos"})

ESTADO_SEM_PACOTE = "sem_pacote"
ESTADO_FIXADO = "fixado"
ESTADOS = (ESTADO_SEM_PACOTE, ESTADO_FIXADO)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_GERADO_EM = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_MODULE_NAME = "_verify_rco_k2_pin_rco_manifesto"


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
        lines = [f"[rco-k2-pin:lock] {'PASS' if self.ok else 'FAIL'}"]
        lines.extend(f"  . {check}" for check in self.checks)
        if self.violations:
            lines.append(f"  violacoes ({len(self.violations)}):")
            lines.extend(f"    - {v.render()}" for v in self.violations)
        return "\n".join(lines)


def _reader() -> ModuleType:
    """O leitor K1 (auxiliares de bytes canonicos, JSON estrito e digest), carregado pelo caminho."""
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, REPO_ROOT / READER)
    if spec is None or spec.loader is None:  # pragma: no cover - arquivo do proprio repo
        raise RuntimeError(f"leitor nao carregavel: {READER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module  # dataclasses resolve o modulo por nome
    spec.loader.exec_module(module)
    return module


def _parse_json(raw: bytes, label: str) -> tuple[Any, list[Violation]]:
    """JSON estrito (sem chave repetida, sem NaN/Infinity). Devolve `(valor, violacoes)`."""
    try:
        return _reader().json_estrito(raw), []
    except ValueError as exc:
        if type(exc).__name__ == "_ChaveDuplicadaError":
            return None, [
                Violation(f"{label}-chave-duplicada", f"{label}: chave JSON repetida {exc.args[0]!r}")
            ]
        return None, [Violation(f"{label}-json-invalido", f"{label}: JSON ilegivel ({type(exc).__name__})")]


def load_lock(path: Path) -> tuple[dict[str, Any] | None, list[Violation]]:
    """Le e interpreta o pin. Devolve `(lock_ou_None, violacoes)`; nao levanta excecao."""
    if path.is_symlink() or not path.is_file():
        return None, [Violation("lock-ausente", f"pin nao encontrado (ou link simbolico): {path}")]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, [Violation("lock-ilegivel", f"{path}: {exc}")]
    parsed, found = _parse_json(raw, "lock")
    if found:
        return None, found
    if not isinstance(parsed, dict):
        return None, [Violation("lock-nao-objeto", f"{path}: o topo tem de ser um objeto JSON")]
    return parsed, []


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


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or _GERADO_EM.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _check_lock_shape(lock: dict[str, Any]) -> tuple[Any, list[Violation]]:
    """Forma do lock e coerencia por estado. Devolve `(estado, violacoes)`."""
    violations: list[Violation] = []
    expected_top = {"lock_format", "estado", "provenance", "manifest_pin", "versao"}
    if set(lock) != expected_top:
        violations.append(
            Violation(
                "lock-campos", f"campos do lock: esperado {sorted(expected_top)}, achado {sorted(lock)}"
            )
        )
    fmt = lock.get("lock_format")
    if not isinstance(fmt, dict) or fmt != {"name": LOCK_NAME, "version": LOCK_VERSION}:
        violations.append(
            Violation(
                "lock-formato",
                f"lock_format tem de ser exatamente name={LOCK_NAME!r}, version={LOCK_VERSION!r}",
            )
        )
    estado = lock.get("estado")
    if estado not in ESTADOS:
        violations.append(
            Violation("estado-invalido", f"'estado' deve ser um de {list(ESTADOS)}: {estado!r}")
        )
    prov = lock.get("provenance")
    if not isinstance(prov, dict) or set(prov) != {"rco_repo", "rco_commit_sha"}:
        violations.append(
            Violation("proveniencia", "provenance tem de ter exatamente rco_repo e rco_commit_sha")
        )
        return estado, violations
    if prov.get("rco_repo") != RCO_REPO:
        violations.append(Violation("proveniencia", f"provenance.rco_repo tem de ser {RCO_REPO!r}"))
    commit, pin, versao = prov.get("rco_commit_sha"), lock.get("manifest_pin"), lock.get("versao")
    if estado == ESTADO_SEM_PACOTE:
        if commit is not None or pin is not None or versao is not None:
            violations.append(
                Violation(
                    "estado-incoerente",
                    "'sem_pacote' exige rco_commit_sha, manifest_pin e versao = null",
                )
            )
    elif estado == ESTADO_FIXADO:
        if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
            violations.append(Violation("proveniencia", "'fixado' exige provenance.rco_commit_sha (40 hex)"))
        if not isinstance(versao, str) or not versao:
            violations.append(Violation("versao", "'fixado' exige versao (texto nao vazio)"))
        if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
            violations.append(
                Violation("manifesto", "'fixado' exige manifest_pin com exatamente path e sha256")
            )
        else:
            if pin.get("path") != K2_MANIFEST_PATH:
                violations.append(
                    Violation(
                        "manifesto-caminho",
                        f"manifest_pin.path tem de ser {K2_MANIFEST_PATH!r}: {pin.get('path')!r}",
                    )
                )
            digest = pin.get("sha256")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                violations.append(
                    Violation("sha256-invalido", "manifest_pin.sha256 exige sha256:<64 hex minusculos>")
                )
    return estado, violations


def _scan_dir(repo_root: Path, fixado: bool) -> list[Violation]:
    """A pasta K2 e plana: so os 7 arquivos em `fixado`; ausente ou so README em `sem_pacote`."""
    unsafe = _unsafe_path(repo_root, PurePosixPath(K2_DIR))
    if unsafe is not None:
        return [Violation("link-simbolico", f"pasta {K2_DIR}: {unsafe}")]
    pasta = repo_root / K2_DIR
    if not pasta.exists():
        return [Violation("arquivo-listado-ausente", f"{K2_DIR}/ ausente em 'fixado'")] if fixado else []
    if not pasta.is_dir():
        return [Violation("pasta-invalida", f"{K2_DIR} existe mas nao e pasta")]
    accepted = PACKAGE_FILES if fixado else {README_NAME}
    violations: list[Violation] = []
    for item in sorted(pasta.rglob("*")):
        rel = f"{K2_DIR}/{item.relative_to(pasta).as_posix()}"
        if item.is_symlink():
            violations.append(Violation("link-simbolico", f"{rel} e link simbolico; recusado"))
        elif item.is_dir():
            violations.append(Violation("subpasta-nao-permitida", f"{rel}: {K2_DIR}/ e plana"))
        elif item.parent != pasta or item.name not in accepted:
            violations.append(
                Violation("arquivo-nao-listado", f"{rel} fora do pacote K2 ({sorted(accepted)})")
            )
    return violations


def _read_regular(repo_root: Path, rel: str) -> bytes | None:
    path = repo_root / rel
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _check_data_files(repo_root: Path, arquivos: object) -> list[Violation]:
    rd = _reader()
    if not isinstance(arquivos, dict) or set(arquivos) != set(DATA_FILES):
        achado = sorted(arquivos) if isinstance(arquivos, dict) else arquivos
        return [
            Violation(
                "manifesto-arquivos",
                f"arquivos tem de listar exatamente {sorted(DATA_FILES)}; achado {achado!r}",
            )
        ]
    violations: list[Violation] = []
    for name in DATA_FILES:
        digest = arquivos[name]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            violations.append(
                Violation("sha256-invalido", f"arquivos[{name!r}] exige sha256:<64 hex minusculos>")
            )
            continue
        content = _read_regular(repo_root, f"{K2_DIR}/{name}")
        if content is None:
            violations.append(
                Violation("arquivo-listado-ausente", f"{K2_DIR}/{name} ausente ou nao e arquivo real")
            )
        elif rd.sha256_prefixado(content) != digest:
            violations.append(
                Violation(
                    "sha256-divergente",
                    f"{name}: recalculado {rd.sha256_prefixado(content)} != manifesto {digest}",
                )
            )
    return violations


def _check_manifest(repo_root: Path, lock: dict[str, Any]) -> tuple[dict[str, Any] | None, list[Violation]]:
    """Contrato-k2 §2 contra os bytes da pasta e do lock. Devolve `(manifesto_ou_None, violacoes)`."""
    rd = _reader()
    violations: list[Violation] = []
    raw = _read_regular(repo_root, K2_MANIFEST_PATH)
    if raw is None:
        return None, [
            Violation("arquivo-listado-ausente", f"{K2_MANIFEST_PATH} ausente ou nao e arquivo real")
        ]
    manifest_hash = rd.sha256_prefixado(raw)

    hash_raw = _read_regular(repo_root, f"{K2_DIR}/{K2_HASH_NAME}")
    if hash_raw is None:
        violations.append(
            Violation("arquivo-listado-ausente", f"{K2_DIR}/{K2_HASH_NAME} ausente ou nao e arquivo real")
        )
    elif hash_raw != f"{manifest_hash}\n".encode():
        stripped = hash_raw.decode("utf-8", "replace").strip()
        code = "sha256-divergente" if _SHA256.fullmatch(stripped) else "sha256-invalido"
        violations.append(
            Violation(
                code,
                f"{K2_HASH_NAME} tem de ser exatamente '{manifest_hash}' + LF (recomputado do manifesto)",
            )
        )

    pin = lock.get("manifest_pin")
    if isinstance(pin, dict) and isinstance(pin.get("sha256"), str) and pin["sha256"] != manifest_hash:
        violations.append(
            Violation(
                "sha256-divergente",
                f"manifest_pin.sha256 {pin['sha256']} != manifesto recomputado {manifest_hash}",
            )
        )

    data, found = _parse_json(raw, "manifesto")
    violations.extend(found)
    if found:
        return None, violations
    if not isinstance(data, dict):
        return None, [*violations, Violation("manifesto-nao-objeto", "o manifesto tem de ser um objeto JSON")]
    if rd.bytes_canonicos(data) != raw:
        violations.append(
            Violation("manifesto-nao-canonico", "bytes do manifesto nao sao os canonicos (contrato-k2 §1)")
        )
    if set(data) != MANIFEST_FIELDS:
        violations.append(
            Violation(
                "manifesto-campos",
                f"campos do manifesto: esperado {sorted(MANIFEST_FIELDS)}, achado {sorted(data)}",
            )
        )
    if data.get("formato") != MANIFEST_FORMAT:
        violations.append(
            Violation("manifesto-formato", f"formato tem de ser {MANIFEST_FORMAT!r}: {data.get('formato')!r}")
        )
    if not isinstance(data.get("versao"), str) or not data["versao"]:
        violations.append(Violation("manifesto-versao", "versao tem de ser texto nao vazio"))
    if not isinstance(data.get("commit_rco"), str) or not _COMMIT.fullmatch(data["commit_rco"]):
        violations.append(Violation("manifesto-commit", "commit_rco tem de ter 40 hex minusculos"))
    if not _valid_timestamp(data.get("gerado_em")):
        violations.append(
            Violation("manifesto-gerado-em", "gerado_em tem de ser UTC 'AAAA-MM-DDTHH:MM:SSZ' valido")
        )
    if not isinstance(data.get("ratificado"), bool):
        violations.append(Violation("manifesto-ratificado", "ratificado tem de ser booleano"))
    violations.extend(_check_data_files(repo_root, data.get("arquivos")))

    prov = lock.get("provenance")
    commit = prov.get("rco_commit_sha") if isinstance(prov, dict) else None
    if lock.get("versao") != data.get("versao"):
        violations.append(
            Violation(
                "lock-versao",
                f"lock.versao {lock.get('versao')!r} != manifesto.versao {data.get('versao')!r}",
            )
        )
    if commit != data.get("commit_rco"):
        violations.append(Violation("proveniencia", "provenance.rco_commit_sha != manifesto.commit_rco"))
    return data, violations


def _check_cross_pin(repo_root: Path, k2: dict[str, Any] | None) -> list[Violation]:
    """Com K1 e K2 `fixado`: mesma versao do pacote e mesmo commit (contrato-k2 §2)."""
    k1_lock, _ = load_lock(repo_root / K1_LOCK_PATH)
    if k1_lock is None or k1_lock.get("estado") != ESTADO_FIXADO:
        return []  # sem K1 fixado nao ha o que cruzar (K1 ilegivel e problema do gate K1)
    raw = _read_regular(repo_root, K1_MANIFEST_PATH)
    if raw is None:
        return [Violation("cruzamento-k1", f"K1 fixado mas {K1_MANIFEST_PATH} ausente; nao da para cruzar")]
    data, errs = _parse_json(raw, "manifesto-k1")
    if errs or not isinstance(data, dict):
        return [Violation("cruzamento-k1", f"manifesto K1 ilegivel; nao da para cruzar ({K1_MANIFEST_PATH})")]
    if k2 is None:
        return []
    origem = data.get("origem")
    k1_commit = origem.get("commit_sha") if isinstance(origem, dict) else None
    violations: list[Violation] = []
    if k2.get("versao") != data.get("versao_pacote"):
        violations.append(
            Violation(
                "cruzamento-versao",
                f"K2 versao {k2.get('versao')!r} != K1 versao_pacote {data.get('versao_pacote')!r}",
            )
        )
    if k2.get("commit_rco") != k1_commit:
        violations.append(
            Violation(
                "cruzamento-commit",
                f"K2 commit_rco {k2.get('commit_rco')!r} != K1 origem.commit_sha {k1_commit!r}",
            )
        )
    return violations


def verify_lock(lock: dict[str, Any], *, repo_root: Path) -> list[Violation]:
    """Todas as checagens do pin contra a arvore. Devolve a lista completa de violacoes."""
    estado, violations = _check_lock_shape(lock)
    fixado = estado == ESTADO_FIXADO
    violations.extend(_scan_dir(repo_root, fixado))
    if fixado:
        manifest, found = _check_manifest(repo_root, lock)
        violations.extend(found)
        violations.extend(_check_cross_pin(repo_root, manifest))
    return violations


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_rco_k2_pin",
        description=(
            "Gate de CI: verifica o pin do pacote K2 (Manual do Beneficiario) do rco. Fail-closed: "
            "saida 0 so quando todas as checagens passam. Sem rede."
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
    result = GateResult(
        ok=not found,
        violations=tuple(found),
        checks=(
            f"pin: {lock_path.name} (estado={lock.get('estado')!r})",
            f"{K2_DIR}/: so os {len(PACKAGE_FILES)} arquivos do pacote (ou so {README_NAME} sem pacote)",
        ),
    )
    print(result.render(), file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
