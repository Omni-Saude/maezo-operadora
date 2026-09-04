#!/usr/bin/env python3
"""CI gate: env-var names declared by the Helm chart / Terraform reconcile with what `src/` reads
(DU-02 / R-002).

Why this exists
----------------
`deploy/helm/maezo-tenant/templates/job-migrations.yaml` declared `MAEZO_TENANT`; the only reader,
`src/maezo/platform/migrations/env.py`, has only ever read `MAEZO_TENANT_ID` (with a silent
`"public"` fallback). Every migration applied through Helm therefore ran against the `public`
schema — exit 0, no error, tenancy isolation silently broken on the very first deploy. The
equivalent ECS path (`deploy/aws-ecs/envs/dev-sa-east-1/task-migrations.tf`) had the identical bug
and was already fixed IN ISOLATION — proof that a one-off rename does not stop the class of defect
from recurring on the next sibling path.

This gate makes a chart/TF <-> `src/` env-name drift mechanical rather than something a human has
to notice from prose. It reconciles two directions over four prefixes this repo owns end-to-end —
`MAEZO_` (its own config), `WHATSAPP_` (the WhatsApp webhook receiver's credentials),
`CIBSEVEN_` (the CIB Seven governance-engine client's endpoint config) and `KAFKA_` (the broker
address every consumer/producer needs; `src/maezo/a2a/outbox_relay.py`'s `KAFKA_BOOTSTRAP_SERVERS`
among them) — the exact prefix set DU-02's own register row/unlock-ledger entry names:

  1. **declared, never read**: every `MAEZO_`/`WHATSAPP_`/`CIBSEVEN_`/`KAFKA_`-prefixed env name
     declared in a rendered Helm manifest or in `deploy/**/*.tf` must be read somewhere in `src/`
     (this is the direction that catches `MAEZO_TENANT` directly — that literal string appears
     nowhere in `src/`). A short, individually-justified allowlist (`INFRA_OWNED_DECLARED`) exempts
     names that are genuinely consumed by a THIRD-PARTY process the chart/TF also happens to
     configure (the Kafka broker's own bootstrap env in `service-kafka.tf`; CIB Seven's own Spring
     datasource in the gated-off-by-default in-cluster StatefulSet) — never a maezo Python module.
  2. **required, never declared**: every env name a `src/` `pydantic_settings.BaseSettings` field
     REQUIRES (no default — `ValidationError` at boot, e.g. `WhatsAppWebhookSettings.app_secret` /
     `.verify_token`) must be declared somewhere in the chart/TF. This is the reverse of (1) and
     would have caught DU-02 too had `env.py` modeled `MAEZO_TENANT_ID` as a required
     `BaseSettings` field instead of an `os.environ.get(..., "public")` fallback.

"Read somewhere in `src/`" is resolved as EVERY string literal in `src/**/*.py` that exactly
matches the prefix pattern, UNION every name a `BaseSettings` subclass implies via
`env_prefix + FIELD_NAME.upper()` when the field carries no explicit `alias`/`validation_alias` —
this codebase's own convention is to spell every canonical env name out as a literal constant
(`ENV_PHI_ENDPOINT_URL: Final[str] = "MAEZO_PHI_ENDPOINT_URL"`, `AliasChoices("WHATSAPP_APP_SECRET",
...)`) even when it is later referenced through a constant, so a full literal-string scan has high
recall without needing to trace indirection through arbitrary call chains — a name reached only
through a helper function that BUILDS the string at runtime (e.g. an f-string) is the one
documented blind spot (see `extract_settings_env_names`'s docstring).

Design
------
Mirrors `scripts/ci/check_helm_entrypoints.py`: pure, dependency-light extraction/reconciliation
functions, a thin `render_chart` subprocess wrapper, and a CLI `main`.

Usage (CI / local)
-------------------
    python3 scripts/ci/check_chart_env_reconciliation.py
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CHART = "deploy/helm/maezo-tenant"
DEFAULT_RELEASE = "maezo-ci"
DEFAULT_VALUE_FILES: tuple[str, ...] = ("deploy/helm/maezo-tenant/values-amh.yaml",)
DEFAULT_TF_ROOT = "deploy"
DEFAULT_SRC_DIR = "src/maezo"

#: The four env-name namespaces this repo owns end-to-end (DU-02's own register row/unlock-ledger
#: entry names exactly these). NOT every prefix ever declared in the chart/TF (e.g. `DB_`, `FHIR_`,
#: `TUNNEL_`, `SPRING_`, `OTEL_` are deliberately out of scope — a different reconciliation concern).
PREFIXES: tuple[str, ...] = ("MAEZO_", "WHATSAPP_", "CIBSEVEN_", "KAFKA_")

_NAME_PATTERN = re.compile(r"^(?:" + "|".join(PREFIXES) + r")[A-Z0-9_]*[A-Z0-9]$")

#: `helm template`'s default per-manifest header (no extra flag needed).
_SOURCE_RE = re.compile(r"^#\s*Source:\s*(\S+)\s*$")

#: ECS/Terraform container-definition env entries: `{ name = "X", value = ... }` / `valueFrom = ...`.
_TF_ENV_NAME_RE = re.compile(r'name\s*=\s*"([A-Z][A-Z0-9_]*)"')

#: Reason strings for `INFRA_OWNED_DECLARED`, factored out so the name table below stays scannable.
_KAFKA_BROKER_BOOTSTRAP_REASON = (
    "Kafka BROKER's own bootstrap config (deploy/aws-ecs/envs/dev-sa-east-1/service-kafka.tf) — a "
    "stock Kafka/KRaft image's env-var surface, consumed by the Kafka JVM process itself, never by "
    "a maezo Python module."
)
_CIBSEVEN_DATASOURCE_REASON = (
    "CIB Seven's OWN Spring datasource (statefulset-cibseven.yaml, gated OFF by default via "
    "cibseven.inCluster.enabled=false — pinned defensively even though it does not surface in "
    "today's default render) — the Java governance engine's own config, never maezo Python's."
)

#: Names whose bootstrap env is entirely the Kafka broker JVM's own (see reason above) — every
#: `name = "KAFKA_..."` in `service-kafka.tf` EXCEPT `KAFKA_BOOTSTRAP_SERVERS`, which src/ DOES
#: read (the address every consumer/producer connects to).
_KAFKA_BROKER_OWN_NAMES: tuple[str, ...] = (
    "KAFKA_NODE_ID",
    "KAFKA_PROCESS_ROLES",
    "KAFKA_CONTROLLER_QUORUM_VOTERS",
    "KAFKA_LISTENERS",
    "KAFKA_ADVERTISED_LISTENERS",
    "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP",
    "KAFKA_INTER_BROKER_LISTENER_NAME",
    "KAFKA_CONTROLLER_LISTENER_NAMES",
    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR",
    "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR",
    "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR",
    "KAFKA_AUTO_CREATE_TOPICS_ENABLE",
    "KAFKA_HEAP_OPTS",
)

#: Declared-but-legitimately-unread-by-Python names — each is consumed by a THIRD-PARTY process the
#: chart/TF also happens to configure, never by a maezo Python module. Pinned and individually
#: justified (mirrors the spirit of `spec/processes/dmn/orphans-allowlist.yaml`, but scoped to this
#: gate only — adding an entry here requires the same review as any other CODEOWNED `scripts/ci/`
#: change).
INFRA_OWNED_DECLARED: dict[str, str] = {
    **dict.fromkeys(_KAFKA_BROKER_OWN_NAMES, _KAFKA_BROKER_BOOTSTRAP_REASON),
    "CIBSEVEN_DATABASE_URL": _CIBSEVEN_DATASOURCE_REASON,
}


@dataclass(frozen=True)
class EnvNameRef:
    """One declared-or-required env name, with where it came from."""

    name: str
    source: str


@dataclass
class ReconciliationResult:
    declared_unread: list[EnvNameRef] = field(default_factory=list)
    required_undeclared: list[EnvNameRef] = field(default_factory=list)
    allowlisted: list[EnvNameRef] = field(default_factory=list)
    declared_total: int = 0
    read_total: int = 0
    required_total: int = 0

    @property
    def ok(self) -> bool:
        return not self.declared_unread and not self.required_undeclared

    def render(self) -> str:
        lines = [
            f"check_chart_env_reconciliation: {self.declared_total} declared name(s), "
            f"{self.read_total} read name(s) known to src/, {self.required_total} required "
            f"name(s), {len(self.allowlisted)} allowlisted (infra-owned, non-Python)."
        ]
        for ref in self.declared_unread:
            lines.append(
                f"  DECLARED BUT NEVER READ: `{ref.name}` ({ref.source}) — no string literal or "
                f"BaseSettings field in src/ names it. Typo, or a rename that missed one side?"
            )
        for ref in self.required_undeclared:
            lines.append(
                f"  REQUIRED BUT NEVER DECLARED: `{ref.name}` ({ref.source}) has no default — "
                f"src/ will fail closed at boot unless the chart/TF injects it."
            )
        if self.ok:
            lines.append("  Every declared name is read; every required name is declared. OK.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Declared names — rendered Helm manifests
# ---------------------------------------------------------------------------


def _split_manifests(rendered_text: str) -> list[tuple[str | None, str]]:
    """Split `helm template` output into (source_template, yaml_chunk) pairs on `# Source:` lines."""
    chunks: list[tuple[str | None, str]] = []
    current_source: str | None = None
    current_lines: list[str] = []
    for line in rendered_text.splitlines():
        match = _SOURCE_RE.match(line.strip())
        if match:
            if current_lines:
                chunks.append((current_source, "\n".join(current_lines)))
            current_source = match.group(1)
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        chunks.append((current_source, "\n".join(current_lines)))
    return chunks


def _walk_env_names(obj: object) -> Iterator[str]:
    """Yield every `name` under any `env:` list, at any nesting depth (containers, initContainers,
    a CronJob's nested `jobTemplate`, ...)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "env" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("name"), str):
                        yield item["name"]
            else:
                yield from _walk_env_names(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_env_names(item)


def extract_declared_from_helm(rendered_text: str) -> list[EnvNameRef]:
    """Pure: every prefix-matched `env[].name` in already-rendered Helm YAML text."""
    refs: list[EnvNameRef] = []
    for source, chunk in _split_manifests(rendered_text):
        try:
            docs = list(yaml.safe_load_all(chunk))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind", "?")
            resource_name = (doc.get("metadata") or {}).get("name", "?")
            label = f"{source or '?'} ({kind}/{resource_name})"
            for name in _walk_env_names(doc):
                if _NAME_PATTERN.match(name):
                    refs.append(EnvNameRef(name=name, source=label))
    return refs


# ---------------------------------------------------------------------------
# Declared names — Terraform
# ---------------------------------------------------------------------------


def extract_declared_from_terraform(tf_root: Path) -> list[EnvNameRef]:
    """Pure-ish (filesystem read only): every prefix-matched `name = "X"` in `<tf_root>/**/*.tf`."""
    refs: list[EnvNameRef] = []
    for tf_path in sorted(tf_root.rglob("*.tf")):
        text = tf_path.read_text(encoding="utf-8", errors="ignore")
        try:
            rel = tf_path.relative_to(REPO_ROOT)
        except ValueError:
            rel = tf_path
        for match in _TF_ENV_NAME_RE.finditer(text):
            name = match.group(1)
            if _NAME_PATTERN.match(name):
                refs.append(EnvNameRef(name=name, source=str(rel)))
    return refs


# ---------------------------------------------------------------------------
# Read names — src/
# ---------------------------------------------------------------------------


def extract_literal_env_names(src_dir: Path) -> set[str]:
    """Every prefix-matched string literal anywhere in `<src_dir>/**/*.py`.

    High recall for this codebase's own convention: every canonical env name is spelled out as a
    literal at least once (`Field(alias="X")`, `AliasChoices("X", ...)`, `NAME: Final[str] = "X"`
    read later via `os.environ.get(NAME)`) — verified against every `MAEZO_`/`WHATSAPP_`/
    `CIBSEVEN_`/`KAFKA_` name this file's own module docstring enumerates. The one documented blind
    spot: a name built at runtime ONLY via string formatting (e.g. an f-string with no matching
    literal anywhere else) is invisible to this scan.
    """
    names: set[str] = set()
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _NAME_PATTERN.fullmatch(node.value)
            ):
                names.add(node.value)
    return names


def _base_class_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _env_prefix_of(class_node: ast.ClassDef) -> str | None:
    """`model_config = SettingsConfigDict(env_prefix="X", ...)` or `model_config = {"env_prefix":
    "X", ...}` — either shape, as seen across this repo's `BaseSettings` subclasses."""
    for stmt in class_node.body:
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "model_config" and value is not None):
            continue
        pairs: list[tuple[object, ast.expr]] = []
        if isinstance(value, ast.Dict):
            pairs = [
                (k.value, v)
                for k, v in zip(value.keys, value.values, strict=True)
                if isinstance(k, ast.Constant)
            ]
        elif isinstance(value, ast.Call):
            pairs = [(kw.arg, kw.value) for kw in value.keywords if kw.arg is not None]
        for key, val in pairs:
            if key == "env_prefix" and isinstance(val, ast.Constant) and isinstance(val.value, str):
                return val.value
    return None


def extract_settings_env_names(src_dir: Path) -> tuple[set[str], set[str]]:
    """AST-scan every `pydantic_settings.BaseSettings` subclass in `<src_dir>/**/*.py`.

    Returns `(all_names, required_names)`:
      - `all_names`: every env name a field implies — its explicit `alias`/`validation_alias`
        literal(s) if given, else `env_prefix + FIELD_NAME.upper()` (pydantic-settings' own
        implicit rule) when the field's default is a plain literal or an in-line `Field(...)` call.
        A field whose default comes from an OPAQUE helper call (e.g. a `_secret(...)` factory that
        builds its alias via an f-string) is skipped rather than guessed — see the module docstring.
      - `required_names`: the subset with NO default (`ValidationError` at boot if unset) — a bare
        annotation, or `Field(...)` with neither `default=` nor `default_factory=` nor a leading
        positional default.
    """
    all_names: set[str] = set()
    required_names: set[str] = set()
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for class_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if "BaseSettings" not in _base_class_names(class_node):
                continue
            env_prefix = _env_prefix_of(class_node)
            for stmt in class_node.body:
                if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                    continue
                field_name = stmt.target.id
                default_value = stmt.value
                aliases: list[str] = []
                required = False
                derivable = True
                if default_value is None:
                    required = True
                elif isinstance(default_value, ast.Call):
                    func = default_value.func
                    func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if func_name == "Field":
                        has_default = any(
                            kw.arg in ("default", "default_factory") for kw in default_value.keywords
                        )
                        if not has_default and not default_value.args:
                            required = True
                        for kw in default_value.keywords:
                            if kw.arg not in ("alias", "validation_alias"):
                                continue
                            alias_value = kw.value
                            if isinstance(alias_value, ast.Constant) and isinstance(alias_value.value, str):
                                aliases.append(alias_value.value)
                            elif isinstance(alias_value, ast.Call):  # AliasChoices("X", "y", ...)
                                for arg in alias_value.args:
                                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                        aliases.append(arg.value)
                    else:
                        derivable = False  # opaque helper — cannot resolve statically
                # else: plain literal default -> optional, still derivable via env_prefix + name.

                names = set(aliases)
                if not names and derivable:
                    names = {(env_prefix or "") + field_name.upper()}
                for name in names:
                    if not _NAME_PATTERN.match(name):
                        continue
                    all_names.add(name)
                    if required:
                        required_names.add(name)
    return all_names, required_names


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile(
    declared: Sequence[EnvNameRef],
    read_names: set[str],
    required: Sequence[EnvNameRef],
) -> ReconciliationResult:
    """Pure: the two directions, given already-extracted declared/read/required sets."""
    result = ReconciliationResult(
        declared_total=len({r.name for r in declared}),
        read_total=len(read_names),
        required_total=len({r.name for r in required}),
    )
    seen_declared: set[str] = set()
    for ref in declared:
        if ref.name in seen_declared:
            continue
        seen_declared.add(ref.name)
        if ref.name in read_names:
            continue
        if ref.name in INFRA_OWNED_DECLARED:
            result.allowlisted.append(ref)
            continue
        result.declared_unread.append(ref)

    declared_names = {r.name for r in declared}
    seen_required: set[str] = set()
    for ref in required:
        if ref.name in seen_required:
            continue
        seen_required.add(ref.name)
        if ref.name not in declared_names:
            result.required_undeclared.append(ref)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def render_chart(
    *,
    chart: str = DEFAULT_CHART,
    release: str = DEFAULT_RELEASE,
    value_files: Sequence[str] = DEFAULT_VALUE_FILES,
    repo_root: Path = REPO_ROOT,
) -> str:
    argv = ["helm", "template", release, chart]
    for vf in value_files:
        argv.extend(["-f", vf])
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, helm is an explicit CI/dev dependency
        argv, cwd=repo_root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(argv)}` failed (exit {proc.returncode}):\n{proc.stderr}")
    return proc.stdout


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--chart", default=DEFAULT_CHART)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    parser.add_argument(
        "-f",
        "--values-file",
        dest="value_files",
        action="append",
        default=None,
        help=f"repeatable; default {list(DEFAULT_VALUE_FILES)}",
    )
    parser.add_argument("--tf-root", default=DEFAULT_TF_ROOT)
    parser.add_argument("--src-dir", default=DEFAULT_SRC_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    value_files = args.value_files if args.value_files is not None else list(DEFAULT_VALUE_FILES)
    try:
        rendered = render_chart(chart=args.chart, release=args.release, value_files=value_files)
    except RuntimeError as exc:
        print(f"check_chart_env_reconciliation: helm template FAILED: {exc}", file=sys.stderr)
        return 1

    src_dir = REPO_ROOT / args.src_dir
    declared = extract_declared_from_helm(rendered) + extract_declared_from_terraform(
        REPO_ROOT / args.tf_root
    )
    literal_names = extract_literal_env_names(src_dir)
    settings_all, settings_required = extract_settings_env_names(src_dir)
    read_names = literal_names | settings_all
    required_refs = [
        EnvNameRef(name=name, source="src/ BaseSettings (no default)") for name in sorted(settings_required)
    ]

    result = reconcile(declared, read_names, required_refs)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
