"""Engine deployment client — pushes `spec/processes/{bpmn,dmn}/**` to CIB Seven.

Talks to the `engine-rest` API exposed by the `cibseven` service in
`docker-compose.yml` (image `cibseven/cibseven:2.1.0`, port 8080, Camunda 7
compatible REST surface). Two operations only:

- ``POST /deployment/create`` — multipart deployment of every `.bpmn`/`.dmn`
  file under `spec/processes/{bpmn,dmn}/`, in ONE deployment call so the
  engine's own duplicate-filtering machinery (``enable-duplicate-filtering``
  + ``deploy-changed-only``) can compare the whole set by ``deployment-name``
  across runs: an unchanged resource is *not* redeployed and does not appear
  in the response's ``deployedXDefinitions`` maps, which is how this module
  tells "deployed" from "skipped" without any local state of its own.
- ``GET /deployment`` — lists what the engine currently holds.

Fail-closed throughout (per T1.3 charter constraint #2): any non-2xx response,
transport error, or unparseable success body raises ``EngineDeployError``
carrying the engine's verbatim error body — BPMN/DMN deploy errors from the
engine are the REAL validation for this tool, so they are never swallowed,
downgraded to a warning, or retried into a false "OK".

`spec/` resolution mirrors `maezo.agents.resolve_spec_dir()` (same
`MAEZO_SPEC_DIR` env override, same fail-closed contract on a missing
directory) — spec/ is the single source of truth (ADR) and this module never
copies artifacts elsewhere before deploying; it reads directly from the
resolved `spec/processes/{bpmn,dmn}/` tree.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from maezo.agents import resolve_spec_dir

#: Environment variable overriding the engine REST base URL.
ENGINE_REST_URL_ENV = "ENGINE_REST_URL"

#: Default engine REST base URL — matches the `cibseven` service in
#: docker-compose.yml (`ports: ["8080:8080"]`, engine-rest mounted at
#: `/engine-rest` inside the CIB Seven webapp).
DEFAULT_ENGINE_REST_URL = "http://localhost:8080/engine-rest"

#: Default deployment name used for engine-side duplicate filtering. Kept
#: STABLE across runs on purpose: `enable-duplicate-filtering` /
#: `deploy-changed-only` compare a submitted resource set against the most
#: recent deployment sharing this name — a different name every run would
#: defeat idempotency entirely (every run would look "new").
DEFAULT_DEPLOYMENT_NAME = "maezo-spec-processes"

#: Response keys the CIB Seven / Camunda 7 `deployment/create` endpoint uses
#: to report resources that were ACTUALLY (re)deployed this call. A resource
#: skipped by duplicate filtering does not appear in any of these maps.
_DEFINITION_RESPONSE_KEYS = (
    "deployedProcessDefinitions",
    "deployedCaseDefinitions",
    "deployedDecisionDefinitions",
    "deployedDecisionRequirementsDefinitions",
)


def resolve_engine_rest_url() -> str:
    """Resolve the engine REST base URL, honoring `ENGINE_REST_URL`.

    Returns:
        The configured base URL, defaulting to the docker-compose `cibseven`
        service's published port (`DEFAULT_ENGINE_REST_URL`).
    """
    return os.environ.get(ENGINE_REST_URL_ENV, DEFAULT_ENGINE_REST_URL)


def resolve_spec_processes_dir(spec_dir: Path | None = None) -> Path:
    """Resolve `spec/processes/`, honoring `MAEZO_SPEC_DIR` (via `maezo.agents`).

    Fail-closed: raises `FileNotFoundError` if the resolved directory does
    not exist — mirrors `maezo.agents.resolve_spec_agents_dir()`.

    Args:
        spec_dir: Optional override for the resolved `spec/` root. Defaults
            to `maezo.agents.resolve_spec_dir()`.

    Returns:
        The resolved, absolute `spec/processes/` directory path.

    Raises:
        FileNotFoundError: If `spec/processes/` does not exist.
    """
    base = spec_dir if spec_dir is not None else resolve_spec_dir()
    processes_dir = base / "processes"
    if not processes_dir.is_dir():
        raise FileNotFoundError(f"spec/processes/ directory not found at {processes_dir}")
    return processes_dir


class EngineDeployError(RuntimeError):
    """Deployment to (or listing from) the engine failed.

    Carries the engine's verbatim response (`status_code` + `body`) when the
    failure came from an HTTP response, so callers can surface it exactly —
    per T1.3 constraint #3, this tool never fabricates or paraphrases what
    the engine said.
    """

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def collect_artifacts(processes_dir: Path) -> list[Path]:
    """Collect every `.bpmn` and `.dmn` file under `processes_dir`, sorted.

    BPMN files are listed before DMN files (stable, readable ordering); each
    group is alphabetically sorted. Non-artifact files (e.g. the DMN
    `orphans-allowlist.yaml` consumed by the validation gate) are ignored —
    this function only ever globs `*.bpmn` / `*.dmn`.

    Args:
        processes_dir: The `spec/processes/` directory (see
            `resolve_spec_processes_dir`).

    Returns:
        A list of artifact file paths (possibly BPMN-only, DMN-only, or
        empty groups if a subdirectory is absent).

    Raises:
        EngineDeployError: If neither `bpmn/` nor `dmn/` yields any files —
            there is nothing to deploy, which is itself a fail-closed
            condition (never silently "succeed" deploying zero artifacts).
    """
    bpmn_dir = processes_dir / "bpmn"
    dmn_dir = processes_dir / "dmn"

    bpmn_files = sorted(bpmn_dir.glob("*.bpmn")) if bpmn_dir.is_dir() else []
    dmn_files = sorted(dmn_dir.glob("*.dmn")) if dmn_dir.is_dir() else []

    if not bpmn_files and not dmn_files:
        raise EngineDeployError(
            f"no .bpmn or .dmn files found under {processes_dir} "
            f"(checked {bpmn_dir} and {dmn_dir}) — nothing to deploy"
        )
    return [*bpmn_files, *dmn_files]


@dataclass(frozen=True, slots=True)
class DeploymentOutcome:
    """Result of one `POST /deployment/create` call.

    `deployed_count` / `skipped_count` are derived from the engine's own
    response — never guessed locally — per T1.3 constraint #3 (never print a
    "deployed" count not obtained from the engine).
    """

    deployment_id: str
    name: str
    submitted: tuple[str, ...]
    redeployed_resources: tuple[str, ...]
    raw: dict[str, Any] = field(repr=False)

    @property
    def deployed_count(self) -> int:
        """Number of resources the engine actually (re)deployed this call."""
        return len(self.redeployed_resources)

    @property
    def skipped_count(self) -> int:
        """Number of submitted resources the engine skipped (duplicate filtering)."""
        return max(len(self.submitted) - self.deployed_count, 0)

    @classmethod
    def from_response(cls, payload: dict[str, Any], *, submitted: Sequence[str]) -> DeploymentOutcome:
        """Build an outcome from the engine's JSON response body.

        Args:
            payload: Parsed JSON body of a successful `deployment/create` response.
            submitted: Filenames that were submitted in this deployment call.

        Returns:
            A `DeploymentOutcome` reflecting exactly what the engine reported.
        """
        resource_names: set[str] = set()
        for key in _DEFINITION_RESPONSE_KEYS:
            definitions = payload.get(key) or {}
            if not isinstance(definitions, dict):
                continue
            for definition in definitions.values():
                if isinstance(definition, dict):
                    resource = definition.get("resource")
                    if resource:
                        resource_names.add(str(resource))
        return cls(
            deployment_id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            submitted=tuple(submitted),
            redeployed_resources=tuple(sorted(resource_names)),
            raw=payload,
        )


class EngineDeployClient:
    """Thin REST client for the CIB Seven `engine-rest` deployment surface.

    Usage:
        with EngineDeployClient() as client:
            outcome = client.deploy(paths, name="maezo-spec-processes")
            deployments = client.list_deployments()
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: Engine REST base URL. Defaults to
                `resolve_engine_rest_url()` (honors `ENGINE_REST_URL`).
            timeout: Per-request timeout in seconds (deployment uploads of 70
                files benefit from a generous default).
            client: Optional pre-built `httpx.Client` (e.g. one built on a
                `httpx.MockTransport` for tests). If provided, this instance
                does NOT own it and will not close it.
        """
        self._base_url = (base_url or resolve_engine_rest_url()).rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    @property
    def base_url(self) -> str:
        return self._base_url

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EngineDeployClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def deploy(self, paths: Sequence[Path], *, name: str) -> DeploymentOutcome:
        """Deploy `paths` as a single named deployment.

        Multipart `POST /deployment/create` with `deployment-name`,
        `enable-duplicate-filtering=true`, `deploy-changed-only=true` — the
        CIB Seven / Camunda 7 REST convention for idempotent, versioned
        deployment (see the T1.3 charter and `docs/runbooks/engine-processes.md`).

        Args:
            paths: Artifact files to deploy (see `collect_artifacts`).
            name: Deployment name (duplicate filtering key).

        Returns:
            A `DeploymentOutcome` reflecting the engine's response.

        Raises:
            EngineDeployError: If `paths` is empty, the request fails at the
                transport level, the engine responds non-2xx (its error body
                is surfaced verbatim), or the success body is not valid JSON.
        """
        if not paths:
            raise EngineDeployError("no artifact files provided to deploy — nothing to send")

        handles: list[BinaryIO] = []
        try:
            files: list[tuple[str, tuple[str, BinaryIO, str]]] = []
            for p in paths:
                fh = p.open("rb")
                handles.append(fh)
                files.append((p.name, (p.name, fh, "application/xml")))

            data = {
                "deployment-name": name,
                "enable-duplicate-filtering": "true",
                "deploy-changed-only": "true",
            }
            try:
                resp = self._client.post(self._url("/deployment/create"), files=files, data=data)
            except httpx.HTTPError as exc:
                raise EngineDeployError(f"deployment '{name}' request to engine failed: {exc}") from exc
        finally:
            for handle in handles:
                handle.close()

        if resp.status_code // 100 != 2:
            raise EngineDeployError(
                f"deployment '{name}' rejected by engine [{resp.status_code}]: {resp.text}",
                status_code=resp.status_code,
                body=resp.text,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise EngineDeployError(
                f"engine returned a 2xx for deployment '{name}' but the body was not valid JSON: "
                f"{resp.text[:500]}"
            ) from exc

        return DeploymentOutcome.from_response(payload, submitted=[p.name for p in paths])

    def list_deployments(self) -> list[dict[str, Any]]:
        """List current deployments (`GET /deployment`).

        Returns:
            The engine's deployment list, most-recent-first per the engine's
            default ordering.

        Raises:
            EngineDeployError: On a transport error or a non-2xx response
                (the engine's error body is surfaced verbatim).
        """
        try:
            resp = self._client.get(self._url("/deployment"))
        except httpx.HTTPError as exc:
            raise EngineDeployError(f"listing deployments failed: {exc}") from exc

        if resp.status_code // 100 != 2:
            raise EngineDeployError(
                f"listing deployments rejected by engine [{resp.status_code}]: {resp.text}",
                status_code=resp.status_code,
                body=resp.text,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise EngineDeployError(
                f"engine returned a 2xx for GET /deployment but the body was not valid JSON: "
                f"{resp.text[:500]}"
            ) from exc

        if not isinstance(payload, list):
            raise EngineDeployError(
                f"engine returned an unexpected GET /deployment body shape (expected a list): "
                f"{resp.text[:500]}"
            )
        return list(payload)
