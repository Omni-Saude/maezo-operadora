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
  `maezo.platform.rco_manifesto` (o mesmo leitor do runtime; so biblioteca padrao): formato MAJOR 1 ou 2
  (2.0.0: `ativacao`/`exige_aditivo` por regra e bloco `ativacao` de `vocabulario.json`),
  sem `origem.sujo`, campos exatos, prefixo `rco_`, anexos (`vocabulario.json`, `entradas.json`,
  `k6-filas.json`) com o sha256 do manifesto e colunas de saida dentro do `saidas[]` de cada decisao.
  Em `config/integrations/rco/k1/` so entram o manifesto e esses anexos (os exemplos do pacote ficam
  fora do lock e fora da arvore: contrato §3). Cada `rco/*.dmn` tem de estar listado com sha256
  igual; nada fora da lista e aceito.

Em ambos os estados: subpasta em `rco/` ou em `k1/`, link simbolico, caminho absoluto ou com `..`,
entrada duplicada e arquivo listado ausente reprovam.

Codigos de saida (mesmos do verificador AMH): 0 = passa; 1 = falha (pin ou arvore); 2 = uso
invalido da linha de comando (argparse).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = "config/integrations/rco/rules.lock.json"
RULES_DIR = "spec/processes/dmn/rco"
#: Pasta plana do manifesto K1 e dos anexos (= `maezo.platform.rco_manifesto.K1_DIR`).
K1_DIR = "config/integrations/rco/k1"
K1_MANIFEST_PATH = f"{K1_DIR}/manifesto.json"
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
        parsed = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except _DuplicateKeyError as exc:
        return None, [Violation("lock-chave-duplicada", f"{path}: chave JSON repetida {exc.args[0]!r}")]
    except (OSError, UnicodeDecodeError) as exc:
        return None, [Violation("lock-ilegivel", f"{path}: {exc}")]
    except json.JSONDecodeError as exc:
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

    violations.extend(_scan_rules_dir(repo_root, set(artifacts) | set(exemplos)))
    violations.extend(_scan_k1_dir(repo_root, estado == ESTADO_FIXADO))
    return violations


def _check_package(
    repo_root: Path, provenance: dict[str, Any], artifacts: dict[PurePosixPath, str]
) -> list[Violation]:
    """Le o manifesto pinado com o leitor do runtime e cruza com o pin (so com manifesto integro)."""
    # Import tardio: o leitor e so biblioteca padrao, mas mora no pacote para que gate e runtime
    # leiam o MESMO formato. `sem_pacote` nem chega aqui.
    from maezo.platform import rco_manifesto as k1

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
    return violations


def _k1_allowed_names(repo_root: Path) -> set[str]:
    """Nomes aceitos em `k1/` no estado `fixado`: o manifesto e os anexos que ele nomeia.

    Leitura so dos nomes (sem validar): a validacao do manifesto e de `_check_package`, que ja reprova
    manifesto divergente ou invalido; aqui so se evita repetir essa falha arquivo a arquivo.
    """
    allowed = {PurePosixPath(K1_MANIFEST_PATH).name}
    try:
        manifesto = json.loads((repo_root / K1_MANIFEST_PATH).read_bytes())
    except (OSError, ValueError):
        return allowed
    if isinstance(manifesto, dict):
        for secao in ("vocabulario", "dicionario_entradas", "k6"):
            nome = manifesto.get(secao, {}).get("path") if isinstance(manifesto.get(secao), dict) else None
            if (
                isinstance(nome, str)
                and nome
                and "/" not in nome
                and "\\" not in nome
                and nome not in (".", "..")
            ):
                allowed.add(nome)
    return allowed


def _scan_k1_dir(repo_root: Path, fixado: bool) -> list[Violation]:
    """`k1/` so aceita o manifesto e os anexos que ele pina; sem pacote, nada (pasta ausente = vazia)."""
    rel_dir = PurePosixPath(K1_DIR)
    unsafe = _unsafe_path(repo_root, rel_dir)
    if unsafe is not None:
        return [Violation("link-simbolico", f"pasta do manifesto K1: {unsafe}")]
    k1_dir = repo_root / K1_DIR
    if not k1_dir.exists():
        return []
    if not k1_dir.is_dir():
        return [Violation("pasta-invalida", f"{K1_DIR} existe mas nao e pasta")]
    allowed = _k1_allowed_names(repo_root) if fixado else set()
    violations: list[Violation] = []
    for path in sorted(k1_dir.rglob("*")):
        rel = PurePosixPath(path.relative_to(repo_root).as_posix())
        if path.is_symlink():
            violations.append(Violation("link-simbolico", f"{rel} e link simbolico; recusado"))
        elif path.is_dir():
            violations.append(Violation("subpasta-nao-permitida", f"{rel}: {K1_DIR}/ e plana"))
        elif rel.parent == rel_dir and rel.name in allowed:
            continue
        else:
            violations.append(
                Violation(
                    "arquivo-nao-listado",
                    f"{rel} nao e o manifesto pinado nem anexo dele (exemplos do pacote ficam fora do lock)",
                )
            )
    return violations


def _scan_rules_dir(repo_root: Path, listed: set[PurePosixPath]) -> list[Violation]:
    """Recusa tudo em `rco/` que o pin nao declara (pasta ausente = vazia)."""
    violations: list[Violation] = []
    rules_dir = repo_root / RULES_DIR
    unsafe = _unsafe_path(repo_root, PurePosixPath(RULES_DIR))
    if unsafe is not None:
        return [Violation("link-simbolico", f"pasta de regras: {unsafe}")]
    if not rules_dir.exists():
        return []
    if not rules_dir.is_dir():
        return [Violation("pasta-invalida", f"{RULES_DIR} existe mas nao e pasta")]
    allowed = set(listed) | {PurePosixPath(RULES_DIR) / README_NAME}
    for path in sorted(rules_dir.rglob("*")):
        rel = PurePosixPath(path.relative_to(repo_root).as_posix())
        if path.is_symlink():
            violations.append(Violation("link-simbolico", f"{rel} e link simbolico; recusado"))
        elif path.is_dir():
            violations.append(
                Violation("subpasta-nao-permitida", f"{rel}: {RULES_DIR}/ nao aceita subpasta (ADR-0075 b)")
            )
        elif rel in allowed:
            continue
        elif rel.suffix == ".dmn":
            violations.append(
                Violation(
                    "dmn-nao-listado",
                    f"{rel} nao esta no pin (nem em artifacts nem em exemplos_sinteticos); "
                    "a pasta recebe so arquivos do K1 via atualizacao do pin",
                )
            )
        else:
            violations.append(Violation("arquivo-nao-listado", f"{rel} nao e README.md nem esta no pin"))
    return violations


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
