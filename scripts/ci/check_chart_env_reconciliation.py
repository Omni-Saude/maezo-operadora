#!/usr/bin/env python3
"""CI gate: EVERY env-var name declared by the Helm chart / Terraform reconciles with what `src/`
reads (DU-02 / R-002).

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
to notice from prose. It reconciles two directions:

  1. **declared, never read**: every env name declared in a rendered Helm manifest or in
     `deploy/**/*.tf` must be read somewhere in `src/` (this is the direction that catches
     `MAEZO_TENANT` directly — that literal string appears nowhere in `src/`).
  2. **required, never declared**: every env name a `src/` `pydantic_settings.BaseSettings` field
     REQUIRES (no default — `ValidationError` at boot, e.g. `WhatsAppWebhookSettings.app_secret` /
     `.verify_token`) must be declared somewhere in the chart/TF. This is the reverse of (1) and
     would have caught DU-02 too had `env.py` modeled `MAEZO_TENANT_ID` as a required
     `BaseSettings` field instead of an `os.environ.get(..., "public")` fallback.

Where the scope came from
--------------------------
The owner's approved decision text (OWNER-DECISIONS-REGISTER R-002, option B, verbatim) asks for a
fence that reconciles "toda variável de env declarada em chart/TF" — EVERY env name, not a subset.
An earlier revision of this file scoped direction (1) to four hardcoded prefixes
(`MAEZO_`/`WHATSAPP_`/`CIBSEVEN_`/`KAFKA_`) and its docstring falsely attributed that scope to
"DU-02's own register row/unlock-ledger entry" — neither document names any prefix; the four-prefix
slice was a scope decision made unilaterally by the implementing task, not the owner (gatekeeper
finding F2). This revision removes the prefix restriction: `_NAME_PATTERN` now matches every
`UPPER_SNAKE_CASE` name regardless of prefix, and every name that direction (1) surfaces as
"declared but not read by `src/`" is either a real defect, fixed in the same PR that introduced
this check (the `MAEZO_TENANT` rename), or one of two exemption tables below — never silently
dropped.

Two reasoned exemption tables (never a prefix or wildcard)
------------------------------------------------------------
  - `INFRA_OWNED_DECLARED`: a declared name genuinely consumed by something other than maezo
    Python — the container runtime, a sidecar, a third-party image/SDK, the JVM, the CPython
    interpreter itself, or an inline script embedded directly in the SAME `.tf`/chart file (not in
    `src/`). Each entry names its actual consumer.
  - `DEFERRED_UNRECONCILED_DECLARED`: a declared name that is genuinely unread by `src/` today, is
    NOT infra-owned, and is NOT the same defect class as `MAEZO_TENANT` (there is no
    differently-spelled reader to rename-fix) — a vestigial or free-form-passthrough value with no
    real consumer found. These are deliberately NOT fixed in this PR (fixing them would be
    speculative — there is no known intended reader to rename toward) and are instead tracked as
    named follow-up gaps in `docs/review-queue.md`, cited in each entry's reason.

Both tables require a non-empty, non-whitespace reason per name — `_require_reasons` (called at
import time) raises if either is violated, so an unreasoned entry cannot silently ship.

"Read somewhere in `src/`" is resolved as the UNION of two context-scoped scans, never a bare
literal-string sweep (gatekeeper finding G3 — a bare sweep also matches plain non-env constants
like `RATIFICADO`/`PASSED`/`FAILED`, which happen to be valid `UPPER_SNAKE_CASE`, so a chart
declaring `- name: PASSED` would pass GREEN with nothing actually reading it):

  1. `extract_literal_env_names`: a name is "read" only when it is the KEY argument of a real
     env-read call — `os.environ.get(KEY, ...)`, `os.getenv(KEY, ...)`, or `os.environ[KEY]` —
     either as a literal directly (`os.environ.get("MAEZO_TENANT_ID", "public")`) OR as a
     module-level `NAME: Final[str] = "MAEZO_TENANT_ID"` (or a plain `NAME = "..."`) constant whose
     literal value is resolved by matching the constant's NAME against every KEY argument that is a
     bare `ast.Name` reference, repo-wide (`ENV_PHI_ENDPOINT_URL: Final[str] =
     "MAEZO_PHI_ENDPOINT_URL"` in `br_regional.py`, later read as
     `os.environ.get(ENV_PHI_ENDPOINT_URL, "")` in `br_resident_provider.py`, a different file). This
     is a same-name heuristic, not true data-flow: two unrelated constants that happen to share a
     Python variable name (but hold different literal values) would cross-pollinate — accepted as
     the deliberate recall/precision trade this repo's own naming convention makes safe in practice,
     and narrower by construction than the prior bare sweep.
  2. `extract_settings_env_names`: every name a `BaseSettings` subclass implies via
     `env_prefix + FIELD_NAME.upper()` when the field carries no explicit `alias`/`validation_alias`,
     or the literal value of an explicit `alias=`/`AliasChoices(...)` — already context-scoped
     (a `BaseSettings` field), unaffected by G3.

A name reached only through a helper function that BUILDS the string at runtime (e.g. an f-string)
is the one documented blind spot (see `extract_settings_env_names`'s docstring) — same as before
G3; what changed is that a literal reached ONLY through prose, a docstring, an unrelated enum/status
string, or any other non-env-read context is now correctly invisible to this scan.

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

#: Every env-var name follows this repo's own convention (UPPER_SNAKE_CASE) regardless of which
#: component owns it — NO prefix restriction (see the module docstring's "Where the scope came
#: from": an earlier revision restricted this to four prefixes and mis-attributed that scope to
#: the register row; the owner's approved text asks for "toda variável de env").
_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")

#: `helm template`'s default per-manifest header (no extra flag needed).
_SOURCE_RE = re.compile(r"^#\s*Source:\s*(\S+)\s*$")

#: ECS/Terraform container-definition env entries: `{ name = "X", value = ... }` / `valueFrom = ...`.
_TF_ENV_NAME_RE = re.compile(r'name\s*=\s*"([A-Z][A-Z0-9_]*)"')


def _require_reasons(allowlist: dict[str, str], *, label: str) -> None:
    """Fail fast (import time) if any allowlist entry has no reason — an unreasoned exemption is
    exactly the "false provenance" failure mode gatekeeper finding F2 flagged in this file's own
    prior revision. Raises `ValueError` naming the offending entry; never silently accepts one."""
    for name, reason in allowlist.items():
        if not reason or not reason.strip():
            raise ValueError(f"{label} entry {name!r} has no reason — every exemption must be justified")


#: Reason strings for `INFRA_OWNED_DECLARED`, factored out so the name table below stays scannable.
_KAFKA_BROKER_BOOTSTRAP_REASON = (
    "Kafka BROKER's own bootstrap config (deploy/aws-ecs/envs/dev-sa-east-1/service-kafka.tf) — a "
    "stock Kafka/KRaft image's env-var surface, consumed by the Kafka JVM process itself, never by "
    "a maezo Python module."
)
_CIBSEVEN_INCLUSTER_DATASOURCE_REASON = (
    "CIB Seven's OWN Spring datasource (statefulset-cibseven.yaml, gated OFF by default via "
    "cibseven.inCluster.enabled=false — pinned defensively even though it does not surface in "
    "today's default render) — the Java governance engine's own config, never maezo Python's."
)
_CIBSEVEN_ECS_JVM_REASON = (
    "CIB Seven's OWN ECS task Spring/JVM config (deploy/aws-ecs/envs/dev-sa-east-1/"
    "service-cibseven.tf) — the Java governance engine's own datasource/JVM env, verified by "
    "reading that file directly; never a maezo Python module."
)
_DSN_COMPOSER_SCRIPT_REASON = (
    "Read by dsn.tf's own inline `python -c` DSN-composer one-liner (`local.dsn_python`, "
    "deploy/aws-ecs/envs/dev-sa-east-1/dsn.tf) — a script embedded directly in that Terraform "
    "file, never `src/`. `DB_USER`/`DB_PASSWORD` are also consumed directly by CIB Seven's own "
    "Spring datasource secrets in service-cibseven.tf."
)
_BOOTSTRAP_DB_SCRIPT_REASON = (
    "Read by the inline `python - <<PY` bootstrap script embedded directly in "
    "deploy/aws-ecs/envs/dev-sa-east-1/task-bootstrap-db.tf's own container `command` — verified "
    "by reading that file directly; never `src/`."
)
_CODEBUILD_BUILDSPEC_REASON = (
    "Read by codebuild.tf's own buildspec shell commands (`$REGISTRO`/`$REPOSITORIO`/`$DOCKERFILE`, "
    "deploy/aws-ecs/envs/dev-sa-east-1/codebuild.tf) — a CodeBuild pipeline variable, never `src/`."
)
_PYTHON_INTERPRETER_REASON = (
    "Read by the CPython interpreter itself before any maezo module executes (disables writing "
    ".pyc files) — never an `os.environ` call in `src/`."
)
_OTEL_SDK_REASON = (
    "Read directly by the `opentelemetry` Python SDK's own auto-config at exporter/span-processor "
    "construction time (the OpenTelemetry spec's own env-var contract) — never via a maezo "
    "`os.environ` literal. Contrast `OTEL_SERVICE_NAME`/`OTEL_EXPORTER_OTLP_ENDPOINT`, which "
    "src/maezo/platform/observability.py DOES read explicitly and are therefore NOT here."
)
_CLOUDFLARED_REASON = (
    "The `cloudflared` third-party binary/image's own env vars "
    "(deploy/aws-ecs/envs/dev-sa-east-1/service-cloudflared.tf) — never maezo Python's."
)

#: Names whose bootstrap env is entirely the Kafka broker JVM's own (see reason above) — every
#: `name = "KAFKA_..."` in `service-kafka.tf` EXCEPT `KAFKA_BOOTSTRAP_SERVERS`, which src/ DOES
#: read (the address every consumer/producer connects to).
_KAFKA_BROKER_OWN_NAMES: tuple[str, ...] = (
    "CLUSTER_ID",
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

#: CIB Seven's own ECS task Spring/JVM names (service-cibseven.tf) — see `_CIBSEVEN_ECS_JVM_REASON`.
_CIBSEVEN_ECS_JVM_NAMES: tuple[str, ...] = (
    "DB_DRIVER",
    "DB_URL",
    "DB_SCHEMA_UPDATE",
    "DB_USERNAME",
    "JAVA_OPTS",
    "TZ",
)

#: Names read by dsn.tf's own inline DSN-composer script — see `_DSN_COMPOSER_SCRIPT_REASON`.
_DSN_COMPOSER_SCRIPT_NAMES: tuple[str, ...] = (
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DSN_SCHEME",
)

#: Names read by task-bootstrap-db.tf's own inline bootstrap script — see
#: `_BOOTSTRAP_DB_SCRIPT_REASON`.
_BOOTSTRAP_DB_SCRIPT_NAMES: tuple[str, ...] = (
    "APP_ROLE",
    "BOOTSTRAP_DB",
    "ENGINE_ROLE",
    "ENGINE_SCHEMA",
    "TARGET_DB",
    "TENANT_SCHEMA",
)

#: Names read by codebuild.tf's own buildspec shell — see `_CODEBUILD_BUILDSPEC_REASON`.
_CODEBUILD_BUILDSPEC_NAMES: tuple[str, ...] = ("DOCKERFILE", "REGISTRO", "REPOSITORIO")

#: OpenTelemetry SDK's own env-var contract — see `_OTEL_SDK_REASON`.
_OTEL_SDK_NAMES: tuple[str, ...] = ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_RESOURCE_ATTRIBUTES")

#: `cloudflared`'s own env vars — see `_CLOUDFLARED_REASON`.
_CLOUDFLARED_NAMES: tuple[str, ...] = ("TUNNEL_LOGLEVEL", "TUNNEL_METRICS", "TUNNEL_TOKEN")

#: Declared-but-legitimately-unread-by-Python names — each is consumed by a THIRD-PARTY process the
#: chart/TF also happens to configure, never by a maezo Python module. Pinned and individually
#: justified (mirrors the spirit of `spec/processes/dmn/orphans-allowlist.yaml`, but scoped to this
#: gate only — adding an entry here requires the same review as any other CODEOWNED `scripts/ci/`
#: change). Every entry's reason is non-empty by construction — `_require_reasons` enforces it below.
INFRA_OWNED_DECLARED: dict[str, str] = {
    **dict.fromkeys(_KAFKA_BROKER_OWN_NAMES, _KAFKA_BROKER_BOOTSTRAP_REASON),
    "CIBSEVEN_DATABASE_URL": _CIBSEVEN_INCLUSTER_DATASOURCE_REASON,
    **dict.fromkeys(_CIBSEVEN_ECS_JVM_NAMES, _CIBSEVEN_ECS_JVM_REASON),
    **dict.fromkeys(_DSN_COMPOSER_SCRIPT_NAMES, _DSN_COMPOSER_SCRIPT_REASON),
    **dict.fromkeys(_BOOTSTRAP_DB_SCRIPT_NAMES, _BOOTSTRAP_DB_SCRIPT_REASON),
    **dict.fromkeys(_CODEBUILD_BUILDSPEC_NAMES, _CODEBUILD_BUILDSPEC_REASON),
    "PYTHONDONTWRITEBYTECODE": _PYTHON_INTERPRETER_REASON,
    **dict.fromkeys(_OTEL_SDK_NAMES, _OTEL_SDK_REASON),
    **dict.fromkeys(_CLOUDFLARED_NAMES, _CLOUDFLARED_REASON),
}
_require_reasons(INFRA_OWNED_DECLARED, label="INFRA_OWNED_DECLARED")

#: Declared-but-unread names that are NEITHER infra-owned NOR the same defect class as
#: `MAEZO_TENANT` (no differently-spelled reader exists to rename toward — see the module
#: docstring). Each reason names the tracked `docs/review-queue.md` follow-up gap; fixing these
#: would be speculative, not a rename, so they are deliberately deferred rather than "fixed" here.
DEFERRED_UNRECONCILED_DECLARED: dict[str, str] = {
    "ENVIRONMENT": (
        "Declared via `agents[].env`'s free-form operator passthrough "
        "(deploy/helm/maezo-tenant/values-amh.yaml, rendered by "
        "`{{- range $k, $v := $agent.env }}` in deployment-agent-runtime.yaml) — an open-ended "
        "extension point, not a fixed chart contract; today's value (`ENVIRONMENT: prod`) has no "
        "reader anywhere in `src/`, spelled the same or differently. NOT a DU-02-class rename bug. "
        "Follow-up gap `HELM-ENV-PASSTHROUGH-UNREAD` (docs/review-queue.md)."
    ),
    "AUDIT_DIR": (
        "Declared for `notifications-bridge` (deployment-bridge.yaml) as a filesystem audit "
        "directory, but `NotificationsBridgeSettings` (src/maezo/platform/integrations/"
        "notifications_bridge.py) has no field for it and the bridge's real audit trail is "
        "`PostgresAuditSink`, not a filesystem path — a vestigial declared name with no reader to "
        "rename toward. Follow-up gap `HELM-ENV-AUDIT-DIR-DEAD` (docs/review-queue.md)."
    ),
}
_require_reasons(DEFERRED_UNRECONCILED_DECLARED, label="DEFERRED_UNRECONCILED_DECLARED")


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
    deferred: list[EnvNameRef] = field(default_factory=list)
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
            f"name(s), {len(self.allowlisted)} allowlisted (infra-owned, non-Python), "
            f"{len(self.deferred)} deferred (tracked follow-up gap, not yet reconciled)."
        ]
        for ref in self.declared_unread:
            lines.append(
                f"  DECLARED BUT NEVER READ: `{ref.name}` ({ref.source}) — not the key argument "
                f"of any `os.environ.get(...)`/`os.getenv(...)`/`os.environ[...]` in src/ "
                f"(directly, or via a module-level constant resolved by name), and no "
                f"`BaseSettings` field aliases it either. Typo, a rename that missed one side, or "
                f"a name genuinely unread — rename to match the real reader, add it to "
                f"INFRA_OWNED_DECLARED with a reason if a third party consumes it, or to "
                f"DEFERRED_UNRECONCILED_DECLARED with a tracked follow-up gap if it is neither."
            )
        for ref in self.required_undeclared:
            # GATEKEEPER FINDING F4 (VERIFY-A2-HELM-CAPACITY.md): the old message hardcoded "has
            # no default ... fails closed at boot" for EVERY required name — false for a
            # `chart_required`-marker-derived name (which DOES have a pydantic default and fails
            # closed at CALL time instead). `ref.source` now carries the full, name-specific
            # explanation (see `_required_env_refs` below) instead of a one-size-fits-all suffix.
            lines.append(f"  REQUIRED BUT NEVER DECLARED: `{ref.name}` ({ref.source})")
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


def _is_os_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "os"


def _is_os_environ(node: ast.expr) -> bool:
    """`os.environ` as an attribute access (never a bare `environ` — this repo never does
    `from os import environ`, verified: `grep -rn 'from os import' src/maezo/` is empty)."""
    return isinstance(node, ast.Attribute) and node.attr == "environ" and _is_os_name(node.value)


def _env_read_key_exprs(tree: ast.AST) -> Iterator[ast.expr]:
    """Yield the KEY expression of every `os.environ.get(KEY, ...)`, `os.getenv(KEY, ...)`, or
    `os.environ[KEY]` call/subscript in `tree` — the three env-read shapes this repo's `src/`
    actually uses (verified: every `os.environ`/`os.getenv` call site in `src/maezo` matches one of
    these three; see G3's module-docstring paragraph)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            is_environ_get = node.func.attr == "get" and _is_os_environ(node.func.value)
            is_os_getenv = node.func.attr == "getenv" and _is_os_name(node.func.value)
            if is_environ_get or is_os_getenv:
                yield node.args[0]
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            # `node.slice` is already the plain expression on this repo's Python (3.12,
            # pyproject.toml) — the `ast.Index` wrapper it would have needed unwrapping from was
            # removed in 3.9. No compatibility shim for a Python this repo does not run.
            yield node.slice


def _module_level_string_constants(src_dir: Path) -> dict[str, set[str]]:
    """name -> the set of `_NAME_PATTERN`-matching string literals ever assigned to that name,
    anywhere in `<src_dir>/**/*.py` (`NAME: Final[str] = "X"` or plain `NAME = "X"`, at any scope —
    this repo's canonical-constant convention is module-level but a class-level one would resolve
    the same way). Resolving a same-named reference elsewhere by this dict is a heuristic, not true
    data-flow — see the module docstring's G3 paragraph.
    """
    constants: dict[str, set[str]] = {}
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            target: ast.expr | None
            value: ast.expr | None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            else:
                continue
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and _NAME_PATTERN.fullmatch(value.value)
            ):
                constants.setdefault(target.id, set()).add(value.value)
    return constants


def extract_literal_env_names(src_dir: Path) -> set[str]:
    """Every env name genuinely READ in `<src_dir>/**/*.py` — a literal is only counted when it is
    the KEY argument of a real env-read call (`os.environ.get`/`os.getenv`/`os.environ[...]`),
    either directly or via a module-level constant resolved by name (see the module docstring's G3
    paragraph for the exact two-pass design and its accepted same-name-heuristic limitation).

    NOT counted: a literal reached only through prose, a docstring, a class attribute unrelated to
    env access, or any other non-env-read context — closing the false-green surface where a
    declared env name spelled like an ordinary constant (`RATIFICADO`, `PASSED`, `FAILED`, ...)
    would otherwise pass this gate with nothing genuinely reading it (gatekeeper finding G3).

    The one remaining documented blind spot (unchanged from before G3): a name built at runtime
    ONLY via string formatting (e.g. an f-string with no matching literal constant anywhere) is
    invisible to this scan.
    """
    constants = _module_level_string_constants(src_dir)
    names: set[str] = set()
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for key_expr in _env_read_key_exprs(tree):
            if isinstance(key_expr, ast.Constant) and isinstance(key_expr.value, str):
                if _NAME_PATTERN.fullmatch(key_expr.value):
                    names.add(key_expr.value)
            elif isinstance(key_expr, ast.Name):
                names.update(constants.get(key_expr.id, ()))
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


def _has_chart_required_marker(field_call: ast.Call) -> bool:
    """`Field(..., json_schema_extra={"chart_required": True})` — a repo-wide, opt-in marker for
    a field that is FUNCTIONALLY required (a fail-closed `ValueError`/refusal somewhere in the
    class's own methods when it's unset at call time) but carries a plain pydantic default, so it
    would otherwise be invisible to the "no default -> required" rule below (WHATSAPP-ENV-PREFIX-b
    / R-101: `WhatsAppSettings.phone_number_id` in `mcp_whatsapp/server.py` is the first user — a
    default `""` that `send_message` refuses to operate on, mirroring the genuinely-required
    `WhatsAppWebhookSettings.app_secret`/`.verify_token` one class over). This is data on the
    field declaration itself (`json_schema_extra` never affects pydantic validation), not a
    hardcoded field-name allowlist — any future field can opt in the same way.
    """
    for kw in field_call.keywords:
        if kw.arg != "json_schema_extra" or not isinstance(kw.value, ast.Dict):
            continue
        for key_node, val_node in zip(kw.value.keys, kw.value.values, strict=True):
            if (
                isinstance(key_node, ast.Constant)
                and key_node.value == "chart_required"
                and isinstance(val_node, ast.Constant)
                and val_node.value is True
            ):
                return True
    return False


def extract_settings_env_names(src_dir: Path) -> tuple[set[str], set[str], set[str]]:
    """AST-scan every `pydantic_settings.BaseSettings` subclass in `<src_dir>/**/*.py`.

    Returns `(all_names, required_names, marker_required_names)`:
      - `all_names`: every env name a field implies — its explicit `alias`/`validation_alias`
        literal(s) if given, else `env_prefix + FIELD_NAME.upper()` (pydantic-settings' own
        implicit rule) when the field's default is a plain literal or an in-line `Field(...)` call.
        A field whose default comes from an OPAQUE helper call (e.g. a `_secret(...)` factory that
        builds its alias via an f-string) is skipped rather than guessed — see the module docstring.
      - `required_names`: the subset with NO default (`ValidationError` at boot if unset) — a bare
        annotation, or `Field(...)` with neither `default=` nor `default_factory=` nor a leading
        positional default — UNION a field carrying the `chart_required` marker (see
        `_has_chart_required_marker`): FUNCTIONALLY required despite having a pydantic default.
      - `marker_required_names`: the SUBSET of `required_names` that reached it ONLY via the
        `chart_required` marker (a field WITH a pydantic default, so it never raises a
        `ValidationError` at boot — the marker means some method fails closed on it at CALL time
        instead). GATEKEEPER FINDING F4 (VERIFY-A2-HELM-CAPACITY.md): callers use this to print an
        honest reason for a marker-derived name instead of the "(no default) ... fails closed at
        boot" wording that is simply false for these fields.
    """
    all_names: set[str] = set()
    required_names: set[str] = set()
    marker_required_names: set[str] = set()
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
                marker_required = False
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
                        if _has_chart_required_marker(default_value):
                            required = True
                            marker_required = True
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
                    if marker_required:
                        marker_required_names.add(name)
    return all_names, required_names, marker_required_names


#: GATEKEEPER FINDING F4 (VERIFY-A2-HELM-CAPACITY.md): the two possible truthful reasons a name
#: can be `required`, keyed by whether it reached that set via the `chart_required` marker or via
#: a genuinely no-default `BaseSettings` field. The marker case explicitly does NOT claim a boot
#: failure — it says the truth: a pydantic default exists, and a fail-closed refusal fires later,
#: at call time, in some method of the class that declared it.
_NO_DEFAULT_REASON = (
    "src/ BaseSettings (no default) — has no default, fails closed at boot unless the chart/TF injects it"
)
_MARKER_REASON = (
    "src/ BaseSettings field carries a pydantic default; `chart_required` marker — functionally "
    "required, fails closed at CALL time (not at boot) unless the chart/TF injects it"
)


def _required_env_refs(required_names: set[str], marker_required_names: set[str]) -> list[EnvNameRef]:
    """Build the `required` side's `EnvNameRef`s with an ACCURATE per-name reason (F4) — pure,
    given the two sets `extract_settings_env_names` already returns, so `main()` and test helpers
    that reconcile the real tree share this exact logic and never drift apart."""
    return [
        EnvNameRef(
            name=name,
            source=_MARKER_REASON if name in marker_required_names else _NO_DEFAULT_REASON,
        )
        for name in sorted(required_names)
    ]


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
        if ref.name in DEFERRED_UNRECONCILED_DECLARED:
            result.deferred.append(ref)
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
    settings_all, settings_required, marker_required = extract_settings_env_names(src_dir)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required)

    result = reconcile(declared, read_names, required_refs)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
