"""W7-7.3 fence — the CONTRACT of `maezo.platform.deploy.engine_deploy`.

COMPLEMENT, not duplicate, of the existing coverage:

- `tests/unit/platform/test_engine_deploy.py` pins BEHAVIOUR: URL building (`:189`), multipart
  assembly + duplicate-filtering flags (`:211-217`), error surfacing (`:245-320`), outcome
  counting (`:151-183`), artifact census (`:109-126`);
- `tests/unit/platform/test_engine_deploy_cli.py` pins CLI wiring, including tenant routing and
  rejection-before-engine (`:361`).

What those suites silently ASSUME — and this fence pins so a silent change alarms:

1. the public contract names keep existing with their kinds (`engine_deploy.py:56-84`,
   `:94`, `:104`, `:132`, `:147`, `:183`, `:237`);
2. `EngineDeployClient.deploy` keeps `name`/`tenant_id` as REQUIRED keywords with no default
   (`engine_deploy.py:285`; the tenant contract of 27/09/2026, docstring `:336-359`) — the CLI
   suites always pass them explicitly, so a sneaky default would go unnoticed there;
3. `ENGINE_REST_URL_ENV` is literally `"ENGINE_REST_URL"` (`engine_deploy.py:56`) — the env var
   real infrastructure injects (`deploy/aws-ecs/envs/dev-sa-east-1/task-deploy-processes.tf:65`,
   `task-engine-bootstrap.tf:44`); the behaviour test (`test_engine_deploy.py:73-75`) sets it
   VIA the constant, so a rename would pass there while decoupling from every task definition;
4. `require_deploy_tenant` is a bounded-token validator (`engine_deploy.py:69-76`) — direct unit
   coverage did not exist (only indirect CLI rejection);
5. `DEFAULT_DEPLOYMENT_NAME` keeps its VALUE (`engine_deploy.py:60-68`): the stability of this
   name IS the engine-side duplicate-filtering idempotency — and its second literal copy in the
   bootstrap tool (`engine_bootstrap/bootstrap.py:466`, `DEPLOYMENT_PRESERVAR` default) stays in
   lockstep (AST cross-check, house `test_auth_business_key.py` style);
6. Wave-1 Q-6 operator-tool posture (`engine_deploy.py` module docstring `:40-52`): the AMBIENT
   `MAEZO_SPEC_DIR` is refused, the EXPLICIT `spec_dir` argument is the sanctioned override —
   pinned here at this module's own boundary for the first time;
7. `DeploymentOutcome` stays a frozen dataclass with exactly the five pinned fields
   (`engine_deploy.py:183-217`) — callers unpack these names.

A legitimate contract change makes these red ON PURPOSE; update the fence in the SAME PR.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import httpx
import pytest

from maezo.agents import RUNTIME_MODE_ENVS, SpecDirOverrideRefusedError
from maezo.platform.deploy.engine_deploy import (
    DEFAULT_DEPLOYMENT_NAME,
    DEFAULT_ENGINE_REST_URL,
    ENGINE_REST_URL_ENV,
    DeploymentOutcome,
    EngineDeployClient,
    EngineDeployError,
    collect_artifacts,
    require_deploy_tenant,
    resolve_engine_rest_url,
    resolve_spec_processes_dir,
)
from maezo.platform.engine_bootstrap import bootstrap as engine_bootstrap_tool

#: O modulo-Ferramenta CLI (NAO o `__init__.py` do pacote) — onde vive o segundo literal.
_BOOTSTRAP_FILE = Path(engine_bootstrap_tool.__file__)


# ---------------------------------------------------------------------------
# 1. Public contract surface
# ---------------------------------------------------------------------------


def test_public_contract_names_exist_and_keep_their_kinds() -> None:
    assert ENGINE_REST_URL_ENV == "ENGINE_REST_URL"
    assert DEFAULT_ENGINE_REST_URL == "http://localhost:8080/engine-rest"
    for fn in (
        require_deploy_tenant,
        resolve_engine_rest_url,
        resolve_spec_processes_dir,
        collect_artifacts,
    ):
        assert callable(fn), fn
    assert issubclass(EngineDeployError, RuntimeError)
    for name in ("deploy", "list_deployments", "close"):
        assert callable(getattr(EngineDeployClient, name)), name


def test_engine_rest_url_env_name_matches_the_infrastructure_injected_variable() -> None:
    """Fence 3: the constant IS the env var name ECS task definitions inject; renaming it would
    silently sever the operator path (`resolve_engine_rest_url` would read a variable nobody
    sets) while `test_engine_deploy.py:73-75` would stay green — it uses the constant itself."""
    assert ENGINE_REST_URL_ENV == "ENGINE_REST_URL"


def test_deploy_signature_keeps_name_and_tenant_id_required_keywords_without_default() -> None:
    """Fence 2: `engine_deploy.py:285` — `tenant_id` REQUIRED keyword, no default (27/09/2026):
    every caller states which side it deploys; `None` is the EXPLICIT shared deployment. The CLI
    suites always pass it, so only this pin catches a default sneaking in."""
    params = inspect.signature(EngineDeployClient.deploy).parameters
    assert list(params) == ["self", "paths", "name", "tenant_id"]
    assert params["paths"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("name", "tenant_id"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is inspect.Parameter.empty, name


# ---------------------------------------------------------------------------
# 4. require_deploy_tenant — the bounded-token contract, directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bom", ["amh", "A", "a" * 40])  # {0,39} after the first char: 40 max
def test_require_deploy_tenant_accepts_bounded_tokens(bom: str) -> None:
    assert require_deploy_tenant(bom) == bom  # passthrough, unchanged


@pytest.mark.parametrize(
    "ruim",
    [
        "",  # missing
        None,  # not a str
        123,  # not a str
        "a" * 41,  # one past the bounded-token ceiling
        "amh\n",  # the trailing-newline hole the `\Z` anchor closes (engine_deploy.py:73)
        "amh ",  # trailing space is not a token character
        "am h",  # inner space
    ],
)
def test_require_deploy_tenant_rejects_everything_else(ruim: object) -> None:
    with pytest.raises(EngineDeployError, match="bounded token"):
        require_deploy_tenant(ruim)


def test_deploy_validates_the_tenant_before_any_file_or_socket(tmp_path: Path) -> None:
    """A bad tenant refuses BEFORE artifacts are opened and BEFORE the engine is contacted —
    the client-level twin of `test_engine_deploy_cli.py:361`. The artifact does not even EXIST:
    if the validation order ever regressed, the failure would be the file's, not the fence's."""

    def handler(request: object) -> object:
        raise AssertionError("the engine must not be called with an unvalidated tenant")

    client = EngineDeployClient(
        base_url="http://engine.example/engine-rest",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EngineDeployError, match="bounded token"):
        client.deploy([tmp_path / "nunca-aberto.bpmn"], name="n", tenant_id="tem espaco")


# ---------------------------------------------------------------------------
# 5. The deployment name is the duplicate-filtering linchpin — value + second copy
# ---------------------------------------------------------------------------


def test_default_deployment_name_keeps_its_idempotency_value() -> None:
    """`engine_deploy.py:60-68`: the name is the key `enable-duplicate-filtering` /
    `deploy-changed-only` compare across runs — a different name every run would make every run
    look new. The VALUE is the contract; this pin makes changing it a conscious act."""
    assert DEFAULT_DEPLOYMENT_NAME == "maezo-spec-processes"


def test_engine_bootstrap_preservar_default_stays_in_lockstep_with_the_deployment_name() -> None:
    """`bootstrap.py:466` carries the SAME literal as its `DEPLOYMENT_PRESERVAR` default: the
    bootstrap tool preserves exactly the deployment the deploy tool creates. Two literals, one
    contract — the AST cross-check (house `test_auth_business_key.py` style) fails when they
    drift, and a LEGITIMATE rename must land on both sides (and this fence) in one PR."""
    tree = ast.parse(_BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    defaults: list[object] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        target = node.func.value
        is_environ = (
            isinstance(target, ast.Attribute)
            and target.attr == "environ"
            and isinstance(target.value, ast.Name)
            and target.value.id == "os"
        )
        if (
            is_environ
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "DEPLOYMENT_PRESERVAR"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
        ):
            defaults.append(node.args[1].value)
    assert defaults == [DEFAULT_DEPLOYMENT_NAME], defaults


# ---------------------------------------------------------------------------
# 6. Wave-1 Q-6 posture at this module's own boundary
# ---------------------------------------------------------------------------


def test_ambient_spec_dir_is_refused_and_the_explicit_flag_is_sanctioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Module docstring `engine_deploy.py:40-52`: with no flag, `resolve_spec_processes_dir`
    inherits `resolve_spec_dir()`'s Q-6 refusal of the ambient `MAEZO_SPEC_DIR`; the EXPLICIT
    `spec_dir` argument never consults the variable at all. Untested at this boundary until
    now."""
    ambient = tmp_path / "ambienta"
    ambient.mkdir()  # deliberately EMPTY: absent a refusal, resolution would FileNotFoundError
    explicit = tmp_path / "explicita"
    (explicit / "processes").mkdir(parents=True)

    for mode_var in RUNTIME_MODE_ENVS:  # undeclared => PRODUCTION (fail-closed default)
        monkeypatch.delenv(mode_var, raising=False)
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(ambient))

    with pytest.raises(SpecDirOverrideRefusedError):
        resolve_spec_processes_dir()
    # The sanctioned flag: resolves WITHOUT consulting the ambient variable.
    assert resolve_spec_processes_dir(explicit) == explicit / "processes"


# ---------------------------------------------------------------------------
# 7. DeploymentOutcome surface
# ---------------------------------------------------------------------------


def test_deployment_outcome_is_frozen_with_exactly_the_pinned_fields() -> None:
    """`engine_deploy.py:183-217` — callers (CLI printers, bootstrap) unpack these names; a
    field removed or renamed is a silent consumer break. `raw` stays excluded from `repr`
    (payload can be large / engine-verbatim)."""
    assert [f.name for f in dataclasses.fields(DeploymentOutcome)] == [
        "deployment_id",
        "name",
        "submitted",
        "redeployed_resources",
        "raw",
    ]
    outcome = DeploymentOutcome.from_response({"id": "d", "name": "n"}, submitted=["a.bpmn"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.deployment_id = "mutado"  # type: ignore[misc]
