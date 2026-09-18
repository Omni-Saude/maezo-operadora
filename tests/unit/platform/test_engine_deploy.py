"""Unit tests for maezo.platform.deploy.engine_deploy — the engine REST client (T1.3).

All HTTP interaction is MOCKED via httpx.MockTransport — these tests exercise
URL building, multipart assembly, and error-body surfacing WITHOUT a running
engine. They are not a substitute for the live-verification run against a
real `cibseven` container (see the T1.3 PR body); they are labeled here as
mocks to make that distinction explicit.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from maezo.platform.deploy.engine_deploy import (
    DEFAULT_DEPLOYMENT_NAME,
    DEFAULT_ENGINE_REST_URL,
    ENGINE_REST_URL_ENV,
    DeploymentOutcome,
    EngineDeployClient,
    EngineDeployError,
    collect_artifacts,
    resolve_engine_rest_url,
    resolve_spec_processes_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response], *, base_url: str = "http://engine.example/engine-rest"
) -> EngineDeployClient:
    """Build an EngineDeployClient backed by a mocked transport (no real network)."""
    transport = httpx.MockTransport(handler)
    return EngineDeployClient(base_url=base_url, client=httpx.Client(transport=transport))


def _deploy_response(
    *,
    deployment_id: str = "dep-1",
    name: str = DEFAULT_DEPLOYMENT_NAME,
    redeployed: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """Build a Camunda-7/CIB-Seven-shaped deployment/create success body."""
    redeployed = redeployed or {}
    body: dict[str, object] = {"id": deployment_id, "name": name}
    for key in (
        "deployedProcessDefinitions",
        "deployedDecisionDefinitions",
        "deployedCaseDefinitions",
        "deployedDecisionRequirementsDefinitions",
    ):
        resources = redeployed.get(key, [])
        body[key] = {f"def-{i}": {"resource": r} for i, r in enumerate(resources)}
    return body


# ---------------------------------------------------------------------------
# resolve_engine_rest_url / resolve_spec_processes_dir
# ---------------------------------------------------------------------------


class TestResolveEngineRestUrl:
    def test_default_matches_compose_port(self) -> None:
        """Default must match the `cibseven` service's published port (8080) in docker-compose.yml."""
        assert resolve_engine_rest_url() == DEFAULT_ENGINE_REST_URL == "http://localhost:8080/engine-rest"

    def test_env_override_is_honored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENGINE_REST_URL_ENV, "http://otherhost:9999/engine-rest")
        assert resolve_engine_rest_url() == "http://otherhost:9999/engine-rest"


class TestResolveSpecProcessesDir:
    def test_default_resolves_to_real_spec_processes(self) -> None:
        processes_dir = resolve_spec_processes_dir()
        assert processes_dir == REPO_ROOT / "spec" / "processes"
        assert (processes_dir / "bpmn").is_dir()
        assert (processes_dir / "dmn").is_dir()

    def test_explicit_spec_dir_override(self, tmp_path: Path) -> None:
        (tmp_path / "processes").mkdir()
        assert resolve_spec_processes_dir(tmp_path) == tmp_path / "processes"

    def test_missing_processes_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="spec/processes"):
            resolve_spec_processes_dir(tmp_path)


# ---------------------------------------------------------------------------
# collect_artifacts
# ---------------------------------------------------------------------------


class TestCollectArtifacts:
    def test_collects_bpmn_and_dmn_sorted(self, tmp_path: Path) -> None:
        bpmn_dir = tmp_path / "bpmn"
        dmn_dir = tmp_path / "dmn"
        bpmn_dir.mkdir()
        dmn_dir.mkdir()
        (bpmn_dir / "b.bpmn").write_text("<x/>")
        (bpmn_dir / "a.bpmn").write_text("<x/>")
        (dmn_dir / "z.dmn").write_text("<x/>")
        (dmn_dir / "y.dmn").write_text("<x/>")
        (dmn_dir / "orphans-allowlist.yaml").write_text("[]")  # not an artifact — must be ignored

        artifacts = collect_artifacts(tmp_path)

        assert [p.name for p in artifacts] == ["a.bpmn", "b.bpmn", "y.dmn", "z.dmn"]

    def test_real_spec_tree_yields_16_bpmn_and_62_dmn(self) -> None:
        """Sanity-checks the artifact census against the committed spec/ tree.

        T1.3 acceptance was 16 BPMN + 54 DMN; T2.7 (artifact phase) added the
        7 ported fraude_scoring decision tables (upcoding_complexity_ceiling,
        unbundling_partial_bundles, phantom_no_diagnosis,
        phantom_suspicious_prefix, frequency_zscore_threshold,
        provider_peer_deviation, risk_thresholds) — 54 + 7 = 61. The GAP-AUTH-4
        criteria gate added `auth_criteria_contratual` (the honest EMPTY contractual
        seam consumed by `operadora.auth.validate_auto_criteria`) — 61 + 1 = 62.
        """
        processes_dir = resolve_spec_processes_dir()
        artifacts = collect_artifacts(processes_dir)
        bpmn = [p for p in artifacts if p.suffix == ".bpmn"]
        dmn = [p for p in artifacts if p.suffix == ".dmn"]
        assert len(bpmn) == 16
        # 62 -> 63: `triage_sufficiency.dmn` (Frente 2.2) entrou na arvore. O arquivo e' nomeado
        # na entrada #407 do CORPUS_DELTA_LOG (test_validation_phi_completeness), com os sete
        # nomes que ele traz e o balde de cada um.
        assert len(dmn) == 63

    def test_empty_tree_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EngineDeployError, match="nothing to deploy"):
            collect_artifacts(tmp_path)

    def test_missing_subdirs_raise(self, tmp_path: Path) -> None:
        (tmp_path / "bpmn").mkdir()  # dmn/ absent, bpmn/ empty
        with pytest.raises(EngineDeployError, match="nothing to deploy"):
            collect_artifacts(tmp_path)


# ---------------------------------------------------------------------------
# DeploymentOutcome
# ---------------------------------------------------------------------------


class TestDeploymentOutcome:
    def test_all_resources_deployed(self) -> None:
        payload = _deploy_response(
            redeployed={"deployedProcessDefinitions": ["a.bpmn"], "deployedDecisionDefinitions": ["b.dmn"]}
        )
        outcome = DeploymentOutcome.from_response(payload, submitted=["a.bpmn", "b.dmn"])
        assert outcome.deployed_count == 2
        assert outcome.skipped_count == 0
        assert outcome.redeployed_resources == ("a.bpmn", "b.dmn")

    def test_duplicate_filtering_yields_all_skipped(self) -> None:
        """No resource in any deployedX map -> everything was a duplicate (idempotent re-run)."""
        payload = _deploy_response()
        outcome = DeploymentOutcome.from_response(payload, submitted=["a.bpmn", "b.dmn"])
        assert outcome.deployed_count == 0
        assert outcome.skipped_count == 2

    def test_partial_deploy_changed_only(self) -> None:
        payload = _deploy_response(redeployed={"deployedProcessDefinitions": ["changed.bpmn"]})
        outcome = DeploymentOutcome.from_response(payload, submitted=["changed.bpmn", "unchanged.bpmn"])
        assert outcome.deployed_count == 1
        assert outcome.skipped_count == 1

    def test_missing_definition_maps_tolerated(self) -> None:
        """A response with no deployedX keys at all (e.g. bare {id, name}) must not raise."""
        outcome = DeploymentOutcome.from_response({"id": "dep-x", "name": "n"}, submitted=["a.bpmn"])
        assert outcome.deployed_count == 0
        assert outcome.skipped_count == 1
        assert outcome.deployment_id == "dep-x"


# ---------------------------------------------------------------------------
# EngineDeployClient.deploy — URL building + multipart assembly
# ---------------------------------------------------------------------------


class TestEngineDeployClientDeployRequest:
    def test_url_building_no_double_slash(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "a.bpmn"
        bpmn.write_text("<x/>")
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return httpx.Response(200, json=_deploy_response())

        # trailing slash on base_url must not produce a double slash in the final URL
        client = _client_with_handler(handler, base_url="http://engine.example/engine-rest/")
        client.deploy([bpmn], name="n")

        assert seen["method"] == "POST"
        assert seen["url"] == "http://engine.example/engine-rest/deployment/create"

    def test_multipart_fields_and_files(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "SP-OP-TEST-001.bpmn"
        bpmn.write_text("<bpmn/>")
        dmn = tmp_path / "some_decision.dmn"
        dmn.write_text("<dmn/>")
        captured: dict[str, bytes] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["content_type"] = request.headers.get("content-type", "").encode()
            captured["body"] = request.read()
            return httpx.Response(200, json=_deploy_response())

        client = _client_with_handler(handler)
        client.deploy([bpmn, dmn], name="my-deployment")

        assert captured["content_type"].startswith(b"multipart/form-data")
        body = captured["body"]
        assert b'name="deployment-name"' in body
        assert b"my-deployment" in body
        assert b'name="enable-duplicate-filtering"' in body
        assert b'name="deploy-changed-only"' in body
        assert b'filename="SP-OP-TEST-001.bpmn"' in body
        assert b'filename="some_decision.dmn"' in body
        # both duplicate-filtering flags must be sent as the string "true"
        assert body.count(b"true") >= 2

    def test_empty_paths_raises_without_request(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
            raise AssertionError("no HTTP request should be made for an empty deploy() call")

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError, match="nothing to send"):
            client.deploy([], name="n")


# ---------------------------------------------------------------------------
# EngineDeployClient.deploy — error surfacing (fail-closed)
# ---------------------------------------------------------------------------


class TestEngineDeployClientErrorSurfacing:
    def test_non_2xx_surfaces_engine_body_verbatim(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "broken.bpmn"
        bpmn.write_text("<not-valid-bpmn>")
        engine_error = (
            '{"type":"ParseException","message":"ENGINE-09008 Could not parse '
            "'broken.bpmn': unexpected end of file\"}"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text=engine_error)

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError) as exc_info:
            client.deploy([bpmn], name="n")

        err = exc_info.value
        assert err.status_code == 400
        assert err.body == engine_error
        assert "ENGINE-09008" in str(err)
        assert "unexpected end of file" in str(err)

    def test_5xx_also_raises(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "a.bpmn"
        bpmn.write_text("<x/>")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal engine error")

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError) as exc_info:
            client.deploy([bpmn], name="n")
        assert exc_info.value.status_code == 500

    def test_transport_error_wrapped(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "a.bpmn"
        bpmn.write_text("<x/>")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError, match="request to engine failed"):
            client.deploy([bpmn], name="n")

    def test_non_json_2xx_body_raises(self, tmp_path: Path) -> None:
        bpmn = tmp_path / "a.bpmn"
        bpmn.write_text("<x/>")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError, match="not valid JSON"):
            client.deploy([bpmn], name="n")

    def test_file_handles_closed_even_on_error(self, tmp_path: Path) -> None:
        """A rejection must not leak open file handles (finally-block close)."""
        bpmn = tmp_path / "a.bpmn"
        bpmn.write_text("<x/>")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="rejected")

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError):
            client.deploy([bpmn], name="n")

        # the file must be re-openable / not left locked by a dangling handle
        with bpmn.open("rb") as fh:
            assert fh.read() == b"<x/>"


# ---------------------------------------------------------------------------
# EngineDeployClient.list_deployments
# ---------------------------------------------------------------------------


class TestEngineDeployClientListDeployments:
    def test_get_url_and_parses_list(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return httpx.Response(200, json=[{"id": "d1", "name": "n1"}, {"id": "d2", "name": "n2"}])

        client = _client_with_handler(handler)
        deployments = client.list_deployments()

        assert seen["method"] == "GET"
        assert seen["url"] == "http://engine.example/engine-rest/deployment"
        assert deployments == [{"id": "d1", "name": "n1"}, {"id": "d2", "name": "n2"}]

    def test_non_2xx_surfaces_engine_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="engine unavailable")

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError) as exc_info:
            client.list_deployments()
        assert exc_info.value.status_code == 503
        assert exc_info.value.body == "engine unavailable"

    def test_unexpected_body_shape_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not": "a list"})

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError, match="unexpected"):
            client.list_deployments()

    def test_transport_error_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = _client_with_handler(handler)
        with pytest.raises(EngineDeployError, match="listing deployments failed"):
            client.list_deployments()


# ---------------------------------------------------------------------------
# client lifecycle
# ---------------------------------------------------------------------------


class TestEngineDeployClientLifecycle:
    def test_context_manager_closes_owned_client(self) -> None:
        client = EngineDeployClient(base_url="http://engine.example/engine-rest")
        with client as c:
            assert c.base_url == "http://engine.example/engine-rest"
        assert client._client.is_closed  # lifecycle assertion

    def test_injected_client_not_closed_by_context_manager(self) -> None:
        real_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        with EngineDeployClient(base_url="http://x/engine-rest", client=real_client):
            pass
        assert not real_client.is_closed
        real_client.close()

    def test_base_url_trailing_slash_stripped(self) -> None:
        client = EngineDeployClient(base_url="http://engine.example/engine-rest///")
        assert client.base_url == "http://engine.example/engine-rest"
