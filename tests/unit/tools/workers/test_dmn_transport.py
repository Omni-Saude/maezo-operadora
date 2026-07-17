"""Unit tests for maezo.tools.workers.dmn_transport (ADR-0028, T1.5).

All HTTP interaction against `CibSevenDmnTransport` is MOCKED via httpx.MockTransport — these
tests exercise URL building, version-resolution caching, typing (`Long` for big cents), and the
fail-closed error table WITHOUT a running engine. They are not a substitute for the live
parity suite against a real `cibseven` container (see the PR body / evidence ledger).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from maezo.tools.workers.dmn_transport import (
    CibSevenDmnTransport,
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    DmnVersion,
    FakeDmnTransport,
    evaluate_sync,
    first_row,
    require_dmn,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = "http://engine.example/engine-rest",
) -> CibSevenDmnTransport:
    """Build a CibSevenDmnTransport backed by a mocked transport (no real network).

    `CibSevenDmnTransport` builds a fresh `httpx.AsyncClient` per call (no persistent client —
    see the class docstring for why: cross-event-loop reuse breaks `evaluate_sync`'s
    `asyncio.run()` bridge), so the test-only mock swap is on `_new_client` (the factory),
    not a single client instance.
    """
    transport = CibSevenDmnTransport(base_url=base_url)
    transport._new_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign] # noqa: SLF001
        base_url=base_url.rstrip("/"),
        transport=httpx.MockTransport(handler),
    )
    return transport


def _definition_body(*, def_id: str = "def-1", version: int = 3, deployment_id: str = "dep-9") -> dict:
    return {"id": def_id, "version": version, "deploymentId": deployment_id, "key": "some_key"}


def _camunda_row(**fields: object) -> dict:
    """Build one CIB-Seven-shaped evaluate() result row: {field: {"value": ..., "type": ...}}."""
    row = {}
    for k, v in fields.items():
        if isinstance(v, bool):
            row[k] = {"value": v, "type": "Boolean"}
        elif isinstance(v, int):
            row[k] = {"value": v, "type": "Integer"}
        elif isinstance(v, float):
            row[k] = {"value": v, "type": "Double"}
        else:
            row[k] = {"value": v, "type": "String"}
    return row


# ---------------------------------------------------------------------------
# DmnTransport Protocol conformance
# ---------------------------------------------------------------------------


def test_cibseven_transport_is_dmn_transport() -> None:
    assert isinstance(CibSevenDmnTransport(base_url="http://x/engine-rest"), DmnTransport)


def test_fake_transport_is_dmn_transport() -> None:
    assert isinstance(FakeDmnTransport(), DmnTransport)


# ---------------------------------------------------------------------------
# CibSevenDmnTransport — version resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_resolves_version_and_evaluates() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[_camunda_row(faixa_valor="DENTRO_TETO_L2")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    rows, version = await transport.evaluate("pagto_alcada", {"valor_pagamento_cents": 100})

    assert rows == [{"faixa_valor": "DENTRO_TETO_L2"}]
    assert version == DmnVersion(key="pagto_alcada", id="def-1", version=3, deployment_id="dep-9")
    # Version lookup + evaluate == 2 requests.
    assert len(calls) == 2
    assert calls[0].endswith("/decision-definition/key/pagto_alcada")
    assert calls[1].endswith("/decision-definition/key/pagto_alcada/evaluate")


@pytest.mark.asyncio
async def test_version_is_cached_across_evaluate_calls() -> None:
    version_lookups = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal version_lookups
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[_camunda_row(x=1)])
        version_lookups += 1
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate("ans_calendar", {"report_type": "RN_124_SIP"})
    await transport.evaluate("ans_calendar", {"report_type": "DIOPS_TRIMESTRAL"})

    assert version_lookups == 1  # cached after the first resolution


@pytest.mark.asyncio
async def test_tenant_scoped_evaluate_uses_tenant_path() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    with pytest.raises(DmnNoResultError):
        first_row(*(await transport.evaluate("some_key", {}, tenant="amh"))[:1], "some_key")

    assert any("/tenant-id/amh" in url for url in calls)


# ---------------------------------------------------------------------------
# Fail-closed error table (ADR-0028 §3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_version_lookup_4xx_raises_dmn_evaluation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    transport = _client_with_handler(handler)
    with pytest.raises(DmnEvaluationError):
        await transport.evaluate("unknown_key", {})


@pytest.mark.asyncio
async def test_version_lookup_unreachable_raises_dmn_evaluation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = _client_with_handler(handler)
    with pytest.raises(DmnEvaluationError):
        await transport.evaluate("pagto_alcada", {})


@pytest.mark.asyncio
async def test_version_response_missing_fields_raises_fail_closed_never_unknown() -> None:
    """The donor's weak fallback (`X-Decision-Definition-Id` header -> `"unknown"`) must NOT
    reappear — an unresolvable version RAISES (ADR-0028 §2), never silently becomes `"unknown"`.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "def-1"})  # missing version/deploymentId

    transport = _client_with_handler(handler)
    with pytest.raises(DmnEvaluationError, match="missing"):
        await transport.evaluate("pagto_alcada", {})


@pytest.mark.asyncio
async def test_evaluate_5xx_raises_dmn_evaluation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(500, text="internal error")
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    with pytest.raises(DmnEvaluationError):
        await transport.evaluate("pagto_alcada", {"valor_pagamento_cents": 100})


@pytest.mark.asyncio
async def test_evaluate_unreachable_raises_dmn_evaluation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    with pytest.raises(DmnEvaluationError):
        await transport.evaluate("pagto_alcada", {"valor_pagamento_cents": 100})


def test_dmn_evaluation_error_is_bare_runtime_error_not_coded() -> None:
    """Must stay a bare RuntimeError (no .code/.message) — see module docstring: this is what
    keeps it classified TRANSIENT (engine-retried) by base.py/harness.py, never reclassified
    into an immediate-incident ValueError."""
    exc = DmnEvaluationError("boom")
    assert isinstance(exc, RuntimeError)
    assert not hasattr(exc, "code")
    assert not hasattr(exc, "message")


def test_dmn_no_result_error_is_coded_for_reclassification() -> None:
    """Must carry .code/.message (this package's coded-exception convention) so
    FunctionWorker.execute() reclassifies it into ValueError -> failure(retries=0)."""
    exc = DmnNoResultError("pagto_alcada", {"valor_pagamento_cents": 1})
    assert exc.code == "ERR_DMN_NO_RESULT"
    assert isinstance(exc.message, str)
    assert exc.decision_key == "pagto_alcada"
    assert not isinstance(exc, RuntimeError)


# ---------------------------------------------------------------------------
# Long typing (ADR-0018 part 2 / ADR-0028 §2) — load-bearing for pagto_alcada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_big_cents_typed_as_long_not_integer() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            import json as jsonlib

            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(faixa_valor="ANALISE_HUMANA")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    # R$50MM = 5_000_000_000 cents — overflows Java int32 (max 2_147_483_647).
    await transport.evaluate("pagto_alcada", {"valor_pagamento_cents": 5_000_000_000})

    sent_var = captured["body"]["variables"]["valor_pagamento_cents"]
    assert sent_var == {"value": 5_000_000_000, "type": "Long"}


@pytest.mark.asyncio
async def test_small_cents_typed_as_integer() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            import json as jsonlib

            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(faixa_valor="DENTRO_TETO_L2")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate("pagto_alcada", {"valor_pagamento_cents": 100})

    sent_var = captured["body"]["variables"]["valor_pagamento_cents"]
    assert sent_var == {"value": 100, "type": "Integer"}


@pytest.mark.asyncio
async def test_boolean_and_string_and_none_typing() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            import json as jsonlib

            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(roteamento="ANALISE_HUMANA")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate(
        "pagto_admissibility",
        {"dados_pagamento_validos": True, "tipo_pagamento": "pix", "lastro_confirmado": None},
    )

    body = captured["body"]["variables"]
    assert body["dados_pagamento_validos"] == {"value": True, "type": "Boolean"}
    assert body["tipo_pagamento"] == {"value": "pix", "type": "String"}
    assert body["lastro_confirmado"] == {"value": None, "type": "String"}


# ---------------------------------------------------------------------------
# FakeDmnTransport — fail-closed on unregistered key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_transport_raises_on_unregistered_key() -> None:
    fake = FakeDmnTransport()
    with pytest.raises(DmnEvaluationError, match="not registered"):
        await fake.evaluate("never_registered", {})


@pytest.mark.asyncio
async def test_fake_transport_returns_registered_rows_and_version() -> None:
    fake = FakeDmnTransport()
    fake.register("ans_retry_policy", [{"backoff": "PT5M", "continue_retry": True}], version=7)

    rows, version = await fake.evaluate("ans_retry_policy", {"retry_attempt": 1})

    assert rows == [{"backoff": "PT5M", "continue_retry": True}]
    assert version.version == 7
    assert version.key == "ans_retry_policy"
    assert fake.calls == [("ans_retry_policy", {"retry_attempt": 1})]


@pytest.mark.asyncio
async def test_fake_transport_close_marks_closed() -> None:
    fake = FakeDmnTransport()
    await fake.close()
    assert fake.closed is True


# ---------------------------------------------------------------------------
# evaluate_sync / first_row — the sync worker-boundary bridge
# ---------------------------------------------------------------------------


def test_evaluate_sync_bridges_fake_transport() -> None:
    fake = FakeDmnTransport()
    fake.register("ans_calendar", [{"due_date": "DRAFT_DUE_DATE"}])

    rows, version = evaluate_sync(fake, "ans_calendar", {"report_type": "RN_124_SIP"})

    assert rows == [{"due_date": "DRAFT_DUE_DATE"}]
    assert version.key == "ans_calendar"


def test_first_row_returns_first_of_multiple() -> None:
    rows = [{"a": 1}, {"a": 2}]
    assert first_row(rows, "some_key") == {"a": 1}


def test_first_row_raises_dmn_no_result_error_on_empty() -> None:
    with pytest.raises(DmnNoResultError) as excinfo:
        first_row([], "pagto_alcada", {"valor_pagamento_cents": 1})
    assert excinfo.value.decision_key == "pagto_alcada"
    assert excinfo.value.code == "ERR_DMN_NO_RESULT"


def test_require_dmn_returns_transport_when_present() -> None:
    fake = FakeDmnTransport()
    assert require_dmn(fake, "operadora.some.topic") is fake


def test_require_dmn_raises_dmn_evaluation_error_when_none() -> None:
    with pytest.raises(DmnEvaluationError, match="operadora.some.topic"):
        require_dmn(None, "operadora.some.topic")


def test_to_audit_dict_shape() -> None:
    version = DmnVersion(key="pagto_alcada", id="def-1", version=3, deployment_id="dep-9")
    assert version.to_audit_dict() == {"version": 3, "id": "def-1", "deploymentId": "dep-9"}
