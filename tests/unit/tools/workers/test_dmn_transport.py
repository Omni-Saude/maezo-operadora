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
    DmnVariableDecodeError,
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
# dict|list -> Json input typing (T1.5 parity fix): `_to_camunda_vars` now mirrors
# `harness.py::_to_camunda_var`'s `dict|list` branch byte-for-byte. Before this fix a bare
# list/dict INPUT value fell through to the catch-all `else` and was typed `String` via Python
# `str(v)` (e.g. `"[1, 2, 3]"` — repr-like text, not guaranteed valid/round-trippable JSON for
# arbitrary content), which the engine would store as an opaque string rather than a structured
# object/array.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_input_typed_as_json() -> None:
    """A bare `list` input value types as Camunda `Json`, `json.dumps`-encoded — exact parity
    with `harness.py::_to_camunda_var`'s `dict|list` branch, never the catch-all `String`
    branch's `str(v)`."""
    import json as jsonlib

    captured: dict = {}
    payload_list = [1, 2, 3]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(faixa_valor="OK")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate("some_key", {"itens": payload_list})

    sent_var = captured["body"]["variables"]["itens"]
    assert sent_var == {
        "value": jsonlib.dumps(payload_list, ensure_ascii=False, default=str),
        "type": "Json",
    }


@pytest.mark.asyncio
async def test_dict_input_typed_as_json() -> None:
    """A bare `dict` input value types as Camunda `Json`, `json.dumps`-encoded — exact parity
    with `harness.py::_to_camunda_var`'s `dict|list` branch."""
    import json as jsonlib

    captured: dict = {}
    payload_dict = {"motivo": "ok", "valor": 10}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(faixa_valor="OK")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate("some_key", {"dossie": payload_dict})

    sent_var = captured["body"]["variables"]["dossie"]
    assert sent_var == {
        "value": jsonlib.dumps(payload_dict, ensure_ascii=False, default=str),
        "type": "Json",
    }


@pytest.mark.asyncio
async def test_nested_structure_round_trips_through_json_typing() -> None:
    """End-to-end parity proof: a nested list/dict INPUT is `Json`-typed by this fix
    (`_to_camunda_vars`) on the way OUT, and — mirroring the pre-existing `Json`-typed
    result-variable decode (`_from_camunda_var`, PR #92, tested below) — decodes back to an
    IDENTICAL Python structure on the way IN. Proves the encode/decode legs of this transport
    are exact wire-format mirrors of each other, same contract as `harness.py`'s
    `_to_camunda_var`/`_from_camunda_var` pair."""
    import json as jsonlib

    nested = {"a": 1, "b": [1, 2, {"c": "x", "d": [True, None]}]}
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            captured["body"] = jsonlib.loads(request.content)
            sent = captured["body"]["variables"]["dossie"]
            # Echo the exact Json-typed wire entry back as a Json-typed RESULT variable, so
            # evaluate()'s existing decode leg (_from_camunda_var) exercises the round trip.
            return httpx.Response(200, json=[{"dossie_echo": sent}])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    rows, _version = await transport.evaluate("some_key", {"dossie": nested})

    sent_var = captured["body"]["variables"]["dossie"]
    assert sent_var == {"value": jsonlib.dumps(nested, ensure_ascii=False, default=str), "type": "Json"}
    assert rows == [{"dossie_echo": nested}]
    assert isinstance(rows[0]["dossie_echo"], dict)


@pytest.mark.asyncio
async def test_pretyped_camunda_var_with_value_key_passes_through_unchanged() -> None:
    """Backward-compat guard (T1.5): a value ALREADY shaped as a Camunda variable
    (`{"value": ..., "type": ...}` — e.g. a caller explicitly forcing `Double` typing) must be
    caught by the dict-with-`"value"` passthrough branch BEFORE the new `dict|list` -> Json
    branch, and pass through completely UNCHANGED — never re-wrapped/re-encoded as Json. This
    is the exact regression this fix must not introduce: the passthrough branch is checked
    first in both `_to_camunda_vars` and `harness.py::_to_camunda_var`."""
    import json as jsonlib

    captured: dict = {}
    pretyped = {"value": 3.14, "type": "Double"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(200, json=[_camunda_row(faixa_valor="OK")])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    await transport.evaluate("some_key", {"taxa": pretyped})

    assert captured["body"]["variables"]["taxa"] == pretyped


# ---------------------------------------------------------------------------
# Json-typed result-variable decode (T1.5 sibling of the harness fix PR #75 and the
# mcp_cibseven/transport.py fix PR #89 — same defect class, this transport's evaluate() leg).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_decodes_json_typed_result_variable() -> None:
    """Real DMN-result wire shape (T1.5 investigation, live-probed against an isolated CIB
    Seven 2.1.0 engine): a decision-table output value wire-typed `Json` arrives in the
    `evaluate` response as a JSON STRING requiring a second decode — the exact same shape
    `tools/workers/harness.py`'s `_from_camunda_var` (PR #75) and `mcp_cibseven/transport.py`'s
    copy (PR #89) already decode on their own read legs. Before this fix, `evaluate()` extracted
    every result-row entry via a bare `v.get("value") if isinstance(v, dict) else v`, so a
    dict/list-valued DMN output would arrive at the calling worker as the raw JSON string
    `'{"a": 1, "b": [1, 2, 3]}'` instead of a Python `dict` — any worker indexing/iterating it
    would break or silently misbehave.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(
                200,
                json=[
                    {
                        "resultado": {
                            "value": '{"a": 1, "b": [1, 2, 3]}',
                            "type": "Json",
                        },
                        "motivo": {"value": "ok", "type": "String"},
                    }
                ],
            )
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    rows, _version = await transport.evaluate("some_key", {"x": 1})

    assert rows == [{"resultado": {"a": 1, "b": [1, 2, 3]}, "motivo": "ok"}]
    assert isinstance(rows[0]["resultado"], dict)  # NOT the raw JSON string


@pytest.mark.asyncio
async def test_evaluate_json_null_value_decodes_to_none() -> None:
    """A `Json`-typed result variable with `value: null` (unset) decodes to `None`, never
    attempted through `json.loads` (which would raise `TypeError` on a non-str/bytes argument)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[{"dossie_vazio": {"value": None, "type": "Json"}}])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    rows, _version = await transport.evaluate("some_key", {})

    assert rows == [{"dossie_vazio": None}]


@pytest.mark.asyncio
async def test_evaluate_malformed_json_result_variable_fails_closed() -> None:
    """Fail-closed contract (mirrors `tools/workers/harness.py`'s `fetch_and_lock` and
    `mcp_cibseven/transport.py`'s `get_process_status` decode contracts, PR #75/#89): malformed
    JSON inside a `Json`-typed DMN result variable must NEVER be handed to the worker as the raw
    string (silent corruption). `evaluate()` raises a coded `DmnVariableDecodeError` instead."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[{"resultado": {"value": "{not-valid-json[", "type": "Json"}}])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    with pytest.raises(DmnVariableDecodeError) as excinfo:
        await transport.evaluate("some_key", {})

    assert excinfo.value.code == "ERR_DMN_VARIABLE_DECODE"
    assert excinfo.value.decision_key == "some_key"
    assert excinfo.value.variable_name == "resultado"


def test_dmn_variable_decode_error_is_coded_not_runtime_error() -> None:
    """Must carry `.code`/`.message` (this package's coded-exception convention) and must NOT be
    a `RuntimeError`/`DmnEvaluationError`: a malformed `Json`-typed result variable on an
    otherwise-successful 200 `evaluate` response is a DETERMINISTIC function of the decision
    table's own rule definitions — retrying the identical `evaluate` call reproduces the
    identical malformed value, so `FunctionWorker.execute()` must reclassify it into `ValueError`
    -> `failure(retries=0)` (immediate incident), exactly like `DmnNoResultError`, NEVER treated
    as transient engine-retryable infrastructure like `DmnEvaluationError`."""
    exc = DmnVariableDecodeError("pagto_alcada", "dossie", ValueError("boom"))
    assert exc.code == "ERR_DMN_VARIABLE_DECODE"
    assert isinstance(exc.message, str)
    assert exc.decision_key == "pagto_alcada"
    assert exc.variable_name == "dossie"
    assert not isinstance(exc, RuntimeError)
    assert not isinstance(exc, DmnEvaluationError)


@pytest.mark.asyncio
async def test_evaluate_non_dict_entry_passes_through_unchanged() -> None:
    """Defensive fallback preserved: a result-row entry that never arrived engine-shaped
    (`{"value": ..., "type": ...}`) passes through unchanged rather than crashing — mirrors the
    pre-fix code's `else v` branch."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            return httpx.Response(200, json=[{"bare": "already-a-string"}])
        return httpx.Response(200, json=_definition_body())

    transport = _client_with_handler(handler)
    rows, _version = await transport.evaluate("some_key", {})

    assert rows == [{"bare": "already-a-string"}]


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
async def test_fake_transport_mirrors_real_transport_decoded_python_objects() -> None:
    """Post-fix contract (T1.5, mirrors `test_fake_get_process_status_mirrors_real_transport_
    decoded_objects` in `tests/unit/tools/test_mcp_cibseven_transport.py` and `test_fake_
    transport_mirrors_real_transport_decoded_python_objects` in `tests/unit/tools/workers/
    test_harness.py`): callers of `evaluate()` see decoded Python objects (dict/list), never a
    raw JSON string — this fake never wire-encodes/decodes at all (`register` stores exactly
    what the test passes in), so it already matches `CibSevenDmnTransport.evaluate()`'s
    post-fix, already-decoded contract by construction."""
    fake = FakeDmnTransport()
    fake.register("SP-OP-CONTAS-001", [{"dossie": {"a": 1, "b": [1, 2, 3]}, "motivo": "ok"}])

    rows, _version = await fake.evaluate("SP-OP-CONTAS-001", {})

    assert rows == [{"dossie": {"a": 1, "b": [1, 2, 3]}, "motivo": "ok"}]
    assert isinstance(rows[0]["dossie"], dict)


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
