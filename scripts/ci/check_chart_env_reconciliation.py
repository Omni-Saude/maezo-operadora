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
     env-read call — `os.environ.get/pop/setdefault(KEY, ...)`, `os.getenv(KEY, ...)`,
     `os.environ[KEY]`, or `KEY in os.environ`/`KEY not in os.environ` — either as a literal
     directly (`os.environ.get("MAEZO_TENANT_ID", "public")`) OR as a module-level `NAME:
     Final[str] = "MAEZO_TENANT_ID"` (or a plain `NAME = "..."`) constant whose literal value is
     resolved by matching the constant's NAME against every KEY argument that is a bare `ast.Name`
     reference, repo-wide (`ENV_PHI_ENDPOINT_URL: Final[str] = "MAEZO_PHI_ENDPOINT_URL"` in
     `br_regional.py`, later read as `os.environ.get(ENV_PHI_ENDPOINT_URL, "")` in
     `br_resident_provider.py`, a different file). This is a same-name heuristic, not true
     data-flow: two unrelated constants that happen to share a Python variable name (but hold
     different literal values) would cross-pollinate — accepted as the deliberate recall/precision
     trade this repo's own naming convention makes safe in practice, and narrower by construction
     than the prior bare sweep.
  2. `extract_settings_env_names`: every name a `BaseSettings` subclass implies via
     `env_prefix + FIELD_NAME.upper()` when the field carries no explicit `alias`/`validation_alias`,
     or the literal value of an explicit `alias=`/`AliasChoices(...)` — already context-scoped
     (a `BaseSettings` field), unaffected by G3.

A name reached only through a helper function that BUILDS the string at runtime (e.g. an f-string)
is the one documented blind spot (see `extract_settings_env_names`'s docstring) — same as before
G3; what changed is that a literal reached ONLY through prose, a docstring, an unrelated enum/status
string, or any other non-env-read context is now correctly invisible to this scan.

`.pop`/`.setdefault`/`in` (B6/V9 finding, 2026-09): `os.environ.get`/`os.getenv`/`os.environ[...]`
were the only three shapes this scan recognized until `src/maezo/gateway/staff_cases/
materialize.py::os.environ.pop("MAEZO_PORTAL_STAFF_SECRET_BUNDLE")` (PR-C, `service-portal.tf:254`
declaring it via a `secrets = [...]` block) went undetected as a read and reported a false
`DECLARED BUT NEVER READ`. `.pop`/`.setdefault` (key-yielding, same species as `.get`) and
`KEY in os.environ`/`KEY not in os.environ` (a membership read) are now recognized.

**§Delta-5 (V9, 2026-09): four more shapes that still SILENTLY PASSED** — resolved a key when one
was statically visible, but did not resolve OR flag when it was not, which is exactly the
silent-pass failure mode this gate exists to close:

  - `os.environ.copy()[KEY]` — resolved: a snapshot copy still names exactly one live key, exactly
    like `os.environ[KEY]`. A bare `.copy()` NOT immediately subscripted (e.g. `env =
    os.environ.copy()`) now fails closed as `UnrecognizedEnvironAccess` instead of being silently
    treated as a harmless whole-mapping op — it might be indexed by something this gate cannot see.
  - `os.environ.update({KEY: ...})` — resolved: every literal-string key of a dict-LITERAL
    argument. `os.environ.update(some_variable)` (the keys are not visible to static analysis)
    fails closed instead.
  - `getattr(os.environ, "get")(KEY)` — resolved when the method-name argument is itself a string
    literal naming a key-yielding method; a non-literal method name, or a literal naming an
    unrecognized method, fails closed.
  - A SIMPLE alias — `e = os.environ` (module- or function-level, any scope, same file-wide
    heuristic as `_module_level_string_constants`) or `from os import environ` (verified empty
    repo-wide as of this writing) — is now tracked (`_file_environ_aliases`), and every
    `alias.get/pop/setdefault(...)`, `alias[...]`, `... in alias`, `alias.copy()[...]`, and
    `alias.update({...})` resolves exactly like the same shape on `os.environ` directly. **Before
    this fix the module docstring called aliasing "out of scope" while the checker neither resolved
    it NOR flagged it as unrecognized — a claim/behaviour mismatch, not a documented limitation**;
    this paragraph is the correction.

Genuinely still out of scope, and now correctly EITHER resolved-elsewhere or simply invisible
(never silently claimed to be handled): an alias reached through an ATTRIBUTE target or a
conditional/parameterized origin — the real shape in `src/maezo/a2a/keyset.py`, `self._environ =
os.environ if environ is None else environ` — is not tracked (full data-flow, not an AST pattern),
and correctly produces neither a resolved key nor an `UnrecognizedEnvironAccess` finding, because
the assignment itself is not an `os.environ.<method>(...)` call this scan inspects at all; a
`getattr` call whose method-NAME argument is not a string literal; and any `os.environ.<method>()`
call whose `<method>` is in neither `_KEY_YIELDING_ENVIRON_METHODS` nor
`_OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS` (`.items`/`.keys`/`.values`/`.clear` only, now that
`.copy`/`.update` are individually classified instead of blanket-opaque) — see
`_environ_access_events` for the single classifying pass all of the above goes through.

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
    "Read by codebuild.tf's own buildspec shell commands (`$REGISTRO`/`$REPOSITORIO`/`$DOCKERFILE`/`$BUILD_ARGS`, "
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
_ADOT_COLLECTOR_REASON = (
    "O binario do coletor ADOT (`aws-otel-collector`, "
    "deploy/aws-ecs/envs/dev-sa-east-1/service-metrics-collector.tf, Frente 5) e' quem le estas "
    "cinco: `AOT_CONFIG_CONTENT` pelo entrypoint da imagem, e as outras quatro pela expansao "
    "`${env:...}` do proprio coletor dentro de "
    "deploy/observability/otel-collector-ecs-dev.yaml. Nenhum modulo Python do maezo participa — o "
    "coletor RASPA o `/metrics` do maezo pela rede, nunca roda dentro dele. Contraste "
    "`OTEL_SERVICE_NAME`/`OTEL_EXPORTER_OTLP_ENDPOINT`, lidas por observability.py e por isso fora "
    "desta tabela."
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

#: Engine nativo (Onda 4, `engine-native.tf`): arquivos que o plugin JAVA do CIB Seven le no boot
#: (`HumanCommandPlugin.java`, `PortalReadPlugin.java`, provedor Q2) — nenhum modulo Python os le.
_ENGINE_NATIVE_JVM_NAMES: tuple[str, ...] = (
    "MAEZO_HUMAN_TRUST_FILE",
    "MAEZO_PORTAL_READ_TRUST_FILE",
    "MAEZO_PORTAL_READ_PROVIDER_FILE",
    "MAEZO_STAFF_COMPOSITION_FILE",
)
_ENGINE_NATIVE_JVM_REASON = (
    "lido pelo plugin Java do engine nativo (HumanCommandPlugin/PortalReadPlugin/provedor Q2), "
    "configurado pelo engine-native.tf; nenhum modulo Python le este nome"
)
#: `case_issuer_runtime.py:58,139-141` le `MAEZO_STAFF_CASE_ISSUER_FILE` por um mapeamento
#: injetavel (`env = os.environ if environ is None else environ; env[ENV]`), forma que a varredura
#: AST nao resolve. Leitor real, nao isencao de nome morto: o sidecar do emissor (B7) o declara.
_STAFF_ISSUER_INJECTED_NAMES: tuple[str, ...] = ("MAEZO_STAFF_CASE_ISSUER_FILE",)
_STAFF_ISSUER_INJECTED_REASON = (
    "lido por case_issuer_runtime.py via mapeamento injetavel env[ENV] (nao resolvido pela AST); "
    "declarado pelo sidecar staff-case-issuer do engine-native.tf"
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
_STAFF_INSTALL_REASON = (
    "ARN inputs of the one-off Onda 3 task (deploy/aws-ecs/envs/dev-sa-east-1/task-staff-install.tf), "
    "consumed by tools/staff_install/installer.py inside the ops image "
    "(deploy/ops/staff-install.Dockerfile) - tooling outside src/, never the application"
)
_STAFF_OPS_NAMES: tuple[str, ...] = (
    "STAFF_OWNER_SECRET_ARN",
    "STAFF_ROWS_SECRET_ARN",
    "STAFF_ADMIN_SECRET_ARN",
    "STAFF_SYN_SECRET_ARN",
    "STAFF_JOB_MATERIALS",
    "STAFF_JOB_LEDGER_BUCKET",
    "STAFF_JOB_LEDGER_KEY",
    "MAEZO_DEV_SYN_AWS_ACCOUNT_ID",
    "MAEZO_DEV_SYN_ENVIRONMENT",
)
_STAFF_OPS_REASON = (
    "inputs of the staff operation tasks (deploy/aws-ecs/envs/dev-sa-east-1/task-staff-ops.tf and the "
    "portal human init), consumed by tools/staff_ops and tools/dev_syn_fixture inside the ops image "
    "(deploy/ops/staff-install.Dockerfile) - tooling outside src/, never the application"
)
_BOOTSTRAP_DB_SCRIPT_NAMES: tuple[str, ...] = (
    "APP_ROLE",
    "BOOTSTRAP_DB",
    "ENGINE_ROLE",
    "ENGINE_SCHEMA",
    "TARGET_DB",
    "TENANT_SCHEMA",
)

#: Names read by codebuild.tf's own buildspec shell — see `_CODEBUILD_BUILDSPEC_REASON`.
_CODEBUILD_BUILDSPEC_NAMES: tuple[str, ...] = ("BUILD_ARGS", "DOCKERFILE", "REGISTRO", "REPOSITORIO")

#: OpenTelemetry SDK's own env-var contract — see `_OTEL_SDK_REASON`.
_OTEL_SDK_NAMES: tuple[str, ...] = ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_RESOURCE_ATTRIBUTES")

#: As cinco do coletor ADOT — see `_ADOT_COLLECTOR_REASON`. `AWS_REGION` entra aqui apesar do nome
#: generico: quem a le' e' a extensao `sigv4auth` do coletor, nao o SDK de nenhum modulo do maezo.
#:
#: `MAEZO_ENV` SAIU desta tupla em 21/09/2026, e a saida e' o comportamento pedido pela propria
#: `test_allowlist_entries_that_are_declared_today_are_genuinely_unread_by_src` ("remove the
#: allowlist entry instead of leaving a stale exemption"): `src/maezo/platform/testchannel/
#: server.py` passou a LER `MAEZO_ENV` — e' por ela que o canal decide se pode aceitar
#: `PORTAL_PUBLIC_ORIGIN` (cerca 3.3 do mandato do portal). A isencao ficou falsa: o nome tem
#: leitor em `src/` agora, entao a direcao (1) da reconciliacao o resolve sozinha e a tabela nao
#: precisa — nem deve — falar dele. O coletor ADOT continua lendo a MESMA variavel pela expansao
#: `${env:...}`; isso nao volta a valer como isencao, porque isencao aqui e' para nome SEM leitor
#: em `src/`, nao para nome com dois consumidores.
_ADOT_COLLECTOR_NAMES: tuple[str, ...] = (
    "AOT_CONFIG_CONTENT",
    "AWS_REGION",
    "MAEZO_CLUSTER",
    "MAEZO_NAMESPACE",
    "AMP_REMOTE_WRITE_URL",
)

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
    **dict.fromkeys(_ENGINE_NATIVE_JVM_NAMES, _ENGINE_NATIVE_JVM_REASON),
    **dict.fromkeys(_STAFF_ISSUER_INJECTED_NAMES, _STAFF_ISSUER_INJECTED_REASON),
    **dict.fromkeys(_DSN_COMPOSER_SCRIPT_NAMES, _DSN_COMPOSER_SCRIPT_REASON),
    **dict.fromkeys(_BOOTSTRAP_DB_SCRIPT_NAMES, _BOOTSTRAP_DB_SCRIPT_REASON),
    **dict.fromkeys(_CODEBUILD_BUILDSPEC_NAMES, _CODEBUILD_BUILDSPEC_REASON),
    "PYTHONDONTWRITEBYTECODE": _PYTHON_INTERPRETER_REASON,
    # task-staff-install.tf: lidos por `tools/staff_install/installer.py` (fora de src/, na imagem de
    # operacao deploy/ops/staff-install.Dockerfile). ARNs, nao segredos.
    "STAFF_INSTALL_ADMIN_SECRET_ARN": _STAFF_INSTALL_REASON,
    "STAFF_INSTALL_LOGIN_SECRET_ARNS": _STAFF_INSTALL_REASON,
    # task-staff-ops.tf (Ondas 4-8): lidos por `tools/staff_ops` e `tools/dev_syn_fixture` (fora de
    # src/, na mesma imagem de operacao). ARNs, pins e o segredo injetado no init; nunca o app.
    **dict.fromkeys(_STAFF_OPS_NAMES, _STAFF_OPS_REASON),
    **dict.fromkeys(_OTEL_SDK_NAMES, _OTEL_SDK_REASON),
    **dict.fromkeys(_CLOUDFLARED_NAMES, _CLOUDFLARED_REASON),
    **dict.fromkeys(_ADOT_COLLECTOR_NAMES, _ADOT_COLLECTOR_REASON),
}
_require_reasons(INFRA_OWNED_DECLARED, label="INFRA_OWNED_DECLARED")

#: Declared-but-unread names that are NEITHER infra-owned NOR the same defect class as
#: `MAEZO_TENANT` (no differently-spelled reader exists to rename toward — see the module
#: docstring). Each reason names the tracked `docs/review-queue.md` follow-up gap; fixing these
#: would be speculative, not a rename, so they are deliberately deferred rather than "fixed" here.
#:
#: EMPTY as of HELM-ENV-AUDIT-DIR-DEAD / HELM-ENV-PASSTHROUGH-UNREAD (2026-09-06): both former
#: entries here — `ENVIRONMENT` (agents[].env passthrough, values-amh.yaml/values-staging.yaml) and
#: `AUDIT_DIR` (notifications-bridge / network-change-bridge / consent-revocation-bridge, all three
#: `deployment-bridge*.yaml`) — were confirmed to have ZERO readers anywhere in `src/` and were
#: REMOVED from the chart entirely (not renamed toward a reader — there was none to rename toward),
#: closing both gaps rather than continuing to defer them. This dict stays declared (not deleted)
#: so a FUTURE genuinely-unreconcilable-but-not-infra-owned name has somewhere to go without
#: re-inventing the table; `_require_reasons` and the empty-dict-safe reconciliation logic both
#: tolerate it being empty.
DEFERRED_UNRECONCILED_DECLARED: dict[str, str] = {}
_require_reasons(DEFERRED_UNRECONCILED_DECLARED, label="DEFERRED_UNRECONCILED_DECLARED")

#: The mirror of the table above for the OTHER direction: required-but-undeclared names whose
#: DEPLOYER is a tracked deploy artifact that has not landed yet. The human portal BFF (ADR-0049
#: D3/D4, `src/maezo/portal/api/config.py::PortalSettings`, `env_prefix="MAEZO_PORTAL_"`) landed
#: DARK in PR-A: no chart/TF launches `maezo.portal.api`, so nothing can fail closed at boot today;
#: its deployer is `deploy/aws-ecs/envs/dev-sa-east-1/service-portal.tf` (PR-C, REPORT-05 slice
#: S8), which declares all eight names. Entries are reported in their OWN bucket (never merged
#: into `allowlisted` or silently dropped), and
#: `tests/unit/ci/test_check_chart_env_reconciliation.py::
#: test_deferred_required_entries_are_genuinely_required_and_undeclared_today` fails the moment an
#: entry stops being genuinely required-and-undeclared — the PR that lands the TF must delete it.
#: ESVAZIADA NESTE PR (PR-C, S8), como o comentario acima exigia: `service-portal.tf` passou a
#: declarar os oito `MAEZO_PORTAL_*` (e mais 15 do staff), entao eles deixaram de ser
#: "required-and-undeclared" e `test_deferred_required_entries_are_genuinely_required_and_
#: undeclared_today` reprovou na hora — o que e' o comportamento desejado da cerca. Fica vazia,
#: como a irma acima, ate' o proximo artefato de deploy que nasca antes do seu TF.
DEFERRED_UNDECLARED_REQUIRED: dict[str, str] = {}
_require_reasons(DEFERRED_UNDECLARED_REQUIRED, label="DEFERRED_UNDECLARED_REQUIRED")


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
    unrecognized_environ: list[UnrecognizedEnvironAccess] = field(default_factory=list)
    deferred_required: list[EnvNameRef] = field(default_factory=list)
    declared_total: int = 0
    read_total: int = 0
    required_total: int = 0

    @property
    def ok(self) -> bool:
        return not self.declared_unread and not self.required_undeclared and not self.unrecognized_environ

    def render(self) -> str:
        lines = [
            f"check_chart_env_reconciliation: {self.declared_total} declared name(s), "
            f"{self.read_total} read name(s) known to src/, {self.required_total} required "
            f"name(s), {len(self.allowlisted)} allowlisted (infra-owned, non-Python), "
            f"{len(self.deferred)} deferred (tracked follow-up gap, not yet reconciled), "
            f"{len(self.unrecognized_environ)} unrecognized os.environ access(es)."
        ]
        for ref in self.declared_unread:
            lines.append(
                f"  DECLARED BUT NEVER READ: `{ref.name}` ({ref.source}) — not the key argument "
                f"of any `os.environ.get/pop/setdefault(...)`, `os.getenv(...)`, `os.environ[...]`, "
                f"or `... in os.environ` in src/ (directly, or via a module-level constant resolved "
                f"by name), and no `BaseSettings` field aliases it either. Typo, a rename that "
                f"missed one side, or a name genuinely unread — rename to match the real reader, "
                f"add it to INFRA_OWNED_DECLARED with a reason if a third party consumes it, or to "
                f"DEFERRED_UNRECONCILED_DECLARED with a tracked follow-up gap if it is neither."
            )
        for ref in self.required_undeclared:
            # GATEKEEPER FINDING F4 (VERIFY-A2-HELM-CAPACITY.md): the old message hardcoded "has
            # no default ... fails closed at boot" for EVERY required name — false for a
            # `chart_required`-marker-derived name (which DOES have a pydantic default and fails
            # closed at CALL time instead). `ref.source` now carries the full, name-specific
            # explanation (see `_required_env_refs` below) instead of a one-size-fits-all suffix.
            lines.append(f"  REQUIRED BUT NEVER DECLARED: `{ref.name}` ({ref.source})")
        for finding in self.unrecognized_environ:
            lines.append(finding.render())
        # Never silently green: a deferred required name is always listed, with its reason.
        for ref in self.deferred_required:
            lines.append(
                f"  REQUIRED, DEPLOYER DEFERRED (DEFERRED_UNDECLARED_REQUIRED): `{ref.name}` — "
                f"{DEFERRED_UNDECLARED_REQUIRED[ref.name]}"
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


def _is_os_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "os"


def _is_os_environ(node: ast.expr) -> bool:
    """`os.environ` as a direct attribute access — never an ALIAS (see `_file_environ_aliases` for
    that; every call site below goes through `_is_environ_like`, which checks both)."""
    return isinstance(node, ast.Attribute) and node.attr == "environ" and _is_os_name(node.value)


def _file_environ_aliases(tree: ast.AST) -> set[str]:
    """Names that are provably `os.environ` itself, within THIS file only (a per-file, whole-file
    heuristic — same recall/precision trade as `_module_level_string_constants`, not true scoped
    data-flow): every `NAME = os.environ` assignment (`e = os.environ; e.pop(k)`, V9 §Delta-5
    finding 4 — the docstring previously called this out-of-scope and never actually resolved it,
    a claim/behaviour mismatch), and the bare name `environ` if `from os import environ` appears
    anywhere in the file (verified empty repo-wide as of this writing:
    `rg -n 'from os import environ' src scripts`; tracked defensively so the day it appears this
    gate already understands it, rather than silently missing it the way `.pop` was missed).

    Reassigning an alias name to something else later in the same file is not modeled — the SAME
    accepted heuristic limitation `_module_level_string_constants` already documents.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "environ":
                    aliases.add(alias.asname or "environ")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and _is_os_environ(node.value):
                aliases.add(target.id)
    return aliases


def _is_environ_like(node: ast.expr, aliases: set[str]) -> bool:
    """`os.environ` itself, or a name `_file_environ_aliases` proved is provably `os.environ` in
    this same file (a simple `NAME = os.environ`/`from os import environ` alias). Deliberately does
    NOT follow an attribute-target assignment (`self._environ = os.environ`,
    `src/maezo/a2a/keyset.py`, real) or a conditional/parameterized origin
    (`os.environ if x is None else x`) — tracking reads through those is full data-flow analysis,
    not an AST pattern match, and stays out of scope (see the module docstring)."""
    return _is_os_environ(node) or (isinstance(node, ast.Name) and node.id in aliases)


#: `os.environ.<method>(KEY, ...)` shapes whose FIRST positional argument this gate resolves to a
#: specific env name. `.pop`/`.setdefault` were added 2026-09 after `.pop("MAEZO_PORTAL_STAFF_
#: SECRET_BUNDLE")` (`src/maezo/gateway/staff_cases/materialize.py`, PR-C) went undetected —
#: `service-portal.tf` declared the name via a `secrets = [...]` block, `src/` genuinely read it via
#: `.pop(...)`, and this gate reported a false `DECLARED BUT NEVER READ` because `.pop` was not one
#: of the two recognized call shapes (B6/V9 finding). `.setdefault` has no current caller in
#: `src/maezo` but is the same species of mutating single-key read and is added defensively so the
#: next one does not repeat this history.
_KEY_YIELDING_ENVIRON_METHODS = frozenset({"get", "pop", "setdefault"})

#: `os.environ.<method>()` operations on the WHOLE mapping that name no single key AND are never
#: themselves further indexed/filtered by a key this gate could resolve — `.items`/`.keys`/
#: `.values`/`.clear` are genuinely always this shape. `.copy`/`.update` are DELIBERATELY ABSENT
#: from this set (V9 §Delta-5 finding 1/2): `os.environ.copy()[KEY]` reads exactly one name (the
#: same as `os.environ[KEY]`, just through a snapshot) and `os.environ.update({KEY: v})` names its
#: keys as literally as a dict literal can — treating either as unconditionally opaque was the
#: silent-pass V9 found. See `_environ_access_events` for how `.copy`/`.update` are actually
#: classified: resolved when the key/keys are literal, an `UnrecognizedEnvironAccess` finding
#: otherwise (never a silent pass either way).
_OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS = frozenset({"items", "keys", "values", "clear"})


@dataclass(frozen=True)
class UnrecognizedEnvironAccess:
    """One `os.environ`/alias access this gate's AST scan cannot attribute to a concrete key.

    Surfaced as a hard finding (never silently dropped) so a NEW `os.environ` access shape added
    anywhere in `<src_dir>` is caught the same PR it lands, instead of being discovered later the
    way `.pop()` was (B6/V9, PR-C `service-portal.tf:254`, 2026-09) — the whole point of a
    fail-closed gate is that "this gate has never seen this shape before" is itself the finding,
    not a reason to pass silently.
    """

    path: str
    lineno: int
    method: str
    #: A fuller, shape-specific description for `render()` — empty for the simple
    #: `os.environ.<method>(...)` case, where `method` alone already renders unambiguously.
    detail: str = ""

    def render(self) -> str:
        shape = self.detail or f"os.environ.{self.method}(...)"
        return (
            f"  UNRECOGNIZED os.environ ACCESS: `{shape}` at {self.path}:{self.lineno} — neither a "
            f"key-yielding form ({sorted(_KEY_YIELDING_ENVIRON_METHODS)}) nor a known whole-mapping "
            f"operation ({sorted(_OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS)}), and no concrete key could "
            "be resolved from it either. Add it to whichever set actually describes it, or make the "
            "key resolvable, in scripts/ci/check_chart_env_reconciliation.py (CODEOWNED) — a form "
            "this gate cannot classify must not be assumed harmless."
        )


def _compare_environ_membership_key(node: ast.Compare, aliases: set[str]) -> ast.expr | None:
    """`KEY in os.environ` / `KEY not in os.environ` (or the same against a tracked alias) -> `KEY`,
    else `None`.

    Only the direct two-term shape is resolved (`ast.Compare(left=KEY, ops=[In|NotIn],
    comparators=[os.environ])`) — a chained comparison (`a in os.environ in b`) is vanishingly rare
    and, if it ever appears, falls through unrecognized rather than being guessed at.
    """
    if (
        len(node.ops) == 1
        and isinstance(node.ops[0], (ast.In, ast.NotIn))
        and len(node.comparators) == 1
        and _is_environ_like(node.comparators[0], aliases)
    ):
        return node.left
    return None


def _environ_access_events(
    tree: ast.AST, aliases: set[str]
) -> Iterator[tuple[str, ast.expr] | tuple[str, tuple[int, str, str]]]:
    """Single classifying walk over every `os.environ`/alias access in `tree`, yielding
    `("key", key_expr)` for everything this gate can attribute to a concrete key expression, or
    `("unrecognized", (lineno, method, detail))` for everything it cannot — never both for the same
    node, and never neither (silently doing nothing is exactly the failure mode this exists to
    close). One pass so a compound shape like `os.environ.copy()[KEY]` is resolved exactly once, at
    the outer `Subscript`, instead of the inner `.copy()` call ALSO being independently flagged as
    unrecognized.

    Recognized shapes -> `"key"`: `os.environ.get/pop/setdefault(KEY, ...)`, `os.getenv(KEY, ...)`,
    `os.environ[KEY]`, `KEY in os.environ`/`KEY not in os.environ`, `os.environ.copy()[KEY]` (same
    resolution as a direct subscript — a copy of the mapping still names exactly one live key),
    `os.environ.update({KEY: ...})` (every literal-string key of a dict-literal argument),
    `getattr(os.environ, "get")(KEY)` (a known key-yielding method reached via `getattr`, its own
    method-name argument a literal).

    Falls through to `"unrecognized"` (V9 §Delta-5, findings 1-4) rather than silently doing
    nothing: a bare `.copy()`/`.update()` not resolvable as above, `getattr(os.environ, X)` where
    `X` is not a literal string or names an unrecognized method, and any `os.environ.<method>()`
    whose `<method>` is in neither `_KEY_YIELDING_ENVIRON_METHODS` nor
    `_OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS`.
    """
    consumed_copy_call_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            if _is_environ_like(node.value, aliases):
                yield ("key", node.slice)
                continue
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "copy"
                and _is_environ_like(call.func.value, aliases)
            ):
                consumed_copy_call_ids.add(id(call))
                yield ("key", node.slice)
                continue
        elif isinstance(node, ast.Compare):
            key = _compare_environ_membership_key(node, aliases)
            if key is not None:
                yield ("key", key)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Call)
                and isinstance(func.func, ast.Name)
                and func.func.id == "getattr"
                and len(func.args) >= 2
                and _is_environ_like(func.args[0], aliases)
            ):
                method_arg = func.args[1]
                if isinstance(method_arg, ast.Constant) and isinstance(method_arg.value, str):
                    method = method_arg.value
                    if method in _KEY_YIELDING_ENVIRON_METHODS and node.args:
                        yield ("key", node.args[0])
                        continue
                    if method in _OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS:
                        continue
                    yield (
                        "unrecognized",
                        (node.lineno, "getattr", f"getattr(os.environ, {method!r})(...)"),
                    )
                    continue
                yield (
                    "unrecognized",
                    (node.lineno, "getattr", "getattr(os.environ, <non-literal method name>)(...)"),
                )
                continue
            if isinstance(func, ast.Attribute) and _is_environ_like(func.value, aliases):
                attr = func.attr
                if attr in _KEY_YIELDING_ENVIRON_METHODS and node.args:
                    yield ("key", node.args[0])
                    continue
                if attr == "update":
                    if node.args and isinstance(node.args[0], ast.Dict):
                        for key_node in node.args[0].keys:
                            if key_node is not None:
                                yield ("key", key_node)
                        continue
                    yield (
                        "unrecognized",
                        (node.lineno, "update", "os.environ.update(<non-dict-literal argument>)"),
                    )
                    continue
                if attr == "copy":
                    if id(node) in consumed_copy_call_ids:
                        continue  # already resolved via its enclosing Subscript, above
                    yield (
                        "unrecognized",
                        (node.lineno, "copy", "os.environ.copy() (not subscripted with a key)"),
                    )
                    continue
                if attr in _OPAQUE_WHOLE_MAPPING_ENVIRON_METHODS:
                    continue
                yield ("unrecognized", (node.lineno, attr, ""))
                continue
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and _is_os_name(func.value)
                and node.args
            ):
                yield ("key", node.args[0])


def _env_read_key_exprs(tree: ast.AST, aliases: set[str]) -> Iterator[ast.expr]:
    """Yield the KEY expression of every recognized `os.environ`/alias/`os.getenv` read in `tree` —
    see `_environ_access_events` for the full list of recognized shapes."""
    for kind, payload in _environ_access_events(tree, aliases):
        if kind == "key":
            yield payload  # type: ignore[misc]


def _unrecognized_environ_accesses(
    tree: ast.AST, rel_path: str, aliases: set[str]
) -> Iterator[UnrecognizedEnvironAccess]:
    """Yield one finding per `os.environ`/alias access `_environ_access_events` could not attribute
    to a concrete key — see `UnrecognizedEnvironAccess`."""
    for kind, payload in _environ_access_events(tree, aliases):
        if kind == "unrecognized":
            lineno, method, detail = payload  # type: ignore[misc]
            yield UnrecognizedEnvironAccess(path=rel_path, lineno=lineno, method=method, detail=detail)


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
    the KEY argument of a real env-read call (`os.environ.get/pop/setdefault`, `os.getenv`,
    `os.environ[...]`, `... in os.environ`, `os.environ.copy()[...]`,
    `os.environ.update({...})`'s literal keys, `getattr(os.environ, "get")(...)`, or any of these
    through a tracked simple alias — see the module docstring's §Delta-5 paragraph), either
    directly or via a module-level constant resolved by name (see the module docstring's G3
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
        aliases = _file_environ_aliases(tree)
        for key_expr in _env_read_key_exprs(tree, aliases):
            if isinstance(key_expr, ast.Constant) and isinstance(key_expr.value, str):
                if _NAME_PATTERN.fullmatch(key_expr.value):
                    names.add(key_expr.value)
            elif isinstance(key_expr, ast.Name):
                names.update(constants.get(key_expr.id, ()))
    return names


def extract_unrecognized_environ_accesses(src_dir: Path) -> list[UnrecognizedEnvironAccess]:
    """Every `os.environ.<method>(...)` call in `<src_dir>/**/*.py` this gate cannot classify as
    either a key-yielding read or a known whole-mapping operation — see `UnrecognizedEnvironAccess`.
    A non-empty result fails this gate closed: a call shape this scan has never seen before might be
    silently reading (or silently NOT reading) a declared name, and the gate must not guess either
    way."""
    findings: list[UnrecognizedEnvironAccess] = []
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
        aliases = _file_environ_aliases(tree)
        findings.extend(_unrecognized_environ_accesses(tree, str(rel), aliases))
    return findings


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


def extract_settings_env_names(src_dir: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    """AST-scan every `pydantic_settings.BaseSettings` subclass in `<src_dir>/**/*.py`.

    Returns `(all_names, required_names, marker_required_names, no_default_names)`:
      - `all_names`: every env name a field implies — its explicit `alias`/`validation_alias`
        literal(s) if given, else `env_prefix + FIELD_NAME.upper()` (pydantic-settings' own
        implicit rule) when the field's default is a plain literal or an in-line `Field(...)` call.
        A field whose default comes from an OPAQUE helper call (e.g. a `_secret(...)` factory that
        builds its alias via an f-string) is skipped rather than guessed — see the module docstring.
      - `required_names`: the UNION of `no_default_names` and `marker_required_names` below — every
        name at least one field makes required, by either route.
      - `no_default_names`: names reached via a field with NO pydantic default — a bare annotation,
        or `Field(...)` with neither `default=` nor `default_factory=` nor a leading positional
        default. Unset -> `ValidationError` at boot (`CrashLoopBackOff`).
      - `marker_required_names`: names reached via a field carrying the `chart_required` marker
        (see `_has_chart_required_marker`) — a field WITH a pydantic default (never raises at
        boot), FUNCTIONALLY required because some method fails closed on it at CALL time instead.
      GATEKEEPER FINDING F10 (§Delta, VERIFY-A2-HELM-CAPACITY.md): these two sets are NOT disjoint
      — the SAME env name can be genuinely no-default on one `BaseSettings` class (e.g.
      `WhatsAppWebhookSettings.verify_token`, `min_length=1`, no default — fails at BOOT) and ALSO
      carry the `chart_required` marker on a SIBLING class's field for the same name (e.g.
      `WhatsAppSettings.whatsapp_verify_token`, default `""`, marker — fails at CALL time). Both
      sets are returned so a caller can give an ACCURATE per-name reason: a name required via
      EITHER no-default route fails at boot regardless of what any marker elsewhere claims, so
      `no_default_names` must take precedence over `marker_required_names` when both are true for
      the same name (GATEKEEPER FINDING F4, corrected by F10 — see `_required_env_refs` below).
    """
    all_names: set[str] = set()
    required_names: set[str] = set()
    marker_required_names: set[str] = set()
    no_default_names: set[str] = set()
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
                no_default = False
                derivable = True
                if default_value is None:
                    required = True
                    no_default = True
                elif isinstance(default_value, ast.Call):
                    func = default_value.func
                    func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if func_name == "Field":
                        has_default = any(
                            kw.arg in ("default", "default_factory") for kw in default_value.keywords
                        )
                        if not has_default and not default_value.args:
                            required = True
                            no_default = True
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
                    if no_default:
                        no_default_names.add(name)
    return all_names, required_names, marker_required_names, no_default_names


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


def _required_env_refs(
    required_names: set[str], marker_required_names: set[str], no_default_names: set[str]
) -> list[EnvNameRef]:
    """Build the `required` side's `EnvNameRef`s with an ACCURATE per-name reason (F4, corrected by
    GATEKEEPER FINDING F10) — pure, given the three sets `extract_settings_env_names` already
    returns, so `main()` and test helpers that reconcile the real tree share this exact logic and
    never drift apart.

    `no_default_names` takes PRECEDENCE over `marker_required_names`: the two are not disjoint (a
    name can be genuinely no-default on one `BaseSettings` class and ALSO carry the marker on a
    sibling class for the same name — e.g. `WHATSAPP_VERIFY_TOKEN` is no-default on
    `WhatsAppWebhookSettings.verify_token` AND marker-carrying on
    `WhatsAppSettings.whatsapp_verify_token`). A name that is no-default ANYWHERE fails at boot
    regardless of what a marker elsewhere claims — printing the marker's "fails at call time"
    reason for such a name would be false, the exact species of bug F4 fixed for the marker-only
    case and F10 fixes for this overlapping case.
    """
    refs: list[EnvNameRef] = []
    for name in sorted(required_names):
        if name in no_default_names:
            source = _NO_DEFAULT_REASON
        elif name in marker_required_names:
            source = _MARKER_REASON
        else:
            source = _NO_DEFAULT_REASON  # unreachable given the caller's invariant; safe fallback
        refs.append(EnvNameRef(name=name, source=source))
    return refs


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile(
    declared: Sequence[EnvNameRef],
    read_names: set[str],
    required: Sequence[EnvNameRef],
    unrecognized_environ: Sequence[UnrecognizedEnvironAccess] = (),
) -> ReconciliationResult:
    """Pure: the two directions, given already-extracted declared/read/required sets, plus any
    `os.environ` access forms the AST scan could not classify (see `UnrecognizedEnvironAccess`) —
    passed straight through into the result so `.ok` fails closed on them too."""
    result = ReconciliationResult(
        declared_total=len({r.name for r in declared}),
        read_total=len(read_names),
        required_total=len({r.name for r in required}),
        unrecognized_environ=list(unrecognized_environ),
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
        if ref.name in declared_names:
            continue
        if ref.name in DEFERRED_UNDECLARED_REQUIRED:
            result.deferred_required.append(ref)
            continue
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
    proc = subprocess.run(  # fixed argv, no shell, helm is an explicit CI/dev dependency
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
    settings_all, settings_required, marker_required, no_default = extract_settings_env_names(src_dir)
    read_names = literal_names | settings_all
    required_refs = _required_env_refs(settings_required, marker_required, no_default)
    unrecognized_environ = extract_unrecognized_environ_accesses(src_dir)

    result = reconcile(declared, read_names, required_refs, unrecognized_environ)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
