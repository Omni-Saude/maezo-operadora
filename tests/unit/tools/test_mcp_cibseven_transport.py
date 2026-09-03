"""Unit tests for `maezo.tools.mcp_cibseven.transport` (ADR-0001, T1.11).

`CibSevenHttpTransport`'s wire-format assertions mirror `dmn_transport.py`'s own test style
(mocked `httpx.AsyncClient`, never a real engine — the real engine is exercised in
`tests/integration/`, ADR-0011). `FakeCibSevenTransport`/`start_process_idempotent` are exercised
directly (no mocking needed — they're pure Python).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenError,
    CibSevenHttpTransport,
    CibSevenVariableDecodeError,
    FakeCibSevenTransport,
    ProcessInstance,
    ProcessNotFoundError,
    historic_instance_is_live,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink

# T-C2 fence: `start_process_idempotent` now requires an audit sink + decision provenance. These
# minimal fixtures let the pre-existing idempotency tests keep exercising the transport behavior;
# the fence itself (required params, emit-before-effect, PHI, dedup) is covered exhaustively in
# tests/unit/tools/test_start_process_fence.py.
_PROV = AgentDecisionProvenance(
    agent_id="helena", agent_version="helena@v0", tenant_id="amh", decision_basis={"route": "escalate"}
)


def _mock_response(json_body: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body)
    if status_code >= 300:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=resp)
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# FakeCibSevenTransport — never imported by production code
# ---------------------------------------------------------------------------


async def test_fake_find_active_instance_none_when_unseeded() -> None:
    fake = FakeCibSevenTransport()
    assert await fake.find_active_instance("ESC-amh-1") is None


async def test_fake_start_process_instance_creates_active() -> None:
    fake = FakeCibSevenTransport()
    inst = await fake.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {"a": 1})
    assert inst.instance_id == "fake-ESC-amh-1"
    assert inst.state == "ACTIVE"
    assert inst.already_existed is False
    assert await fake.find_active_instance("ESC-amh-1") == inst


async def test_fake_get_process_status_raises_when_not_found() -> None:
    fake = FakeCibSevenTransport()
    with pytest.raises(ProcessNotFoundError):
        await fake.get_process_status("nope")


async def test_fake_get_process_status_mirrors_real_transport_decoded_objects() -> None:
    """T1.1 sibling fix: the fake must mirror the real (post-fix) transport's contract —
    callers of `get_process_status` see decoded Python objects (list/dict), never a raw JSON
    string and never a silently-dropped `{}` (mirrors `test_fake_transport_mirrors_real_
    transport_decoded_python_objects` in `tests/unit/tools/workers/test_harness.py`)."""
    fake = FakeCibSevenTransport()
    await fake.start_process_instance(
        "SP-OP-CONTAS-001",
        "CONTAS-amh-1",
        {"linhas_conta_refs": [{"numero_guia_tiss": "G1"}], "numero_lote_tiss": "L1"},
    )

    status = await fake.get_process_status("CONTAS-amh-1")

    assert status.variables["linhas_conta_refs"] == [{"numero_guia_tiss": "G1"}]
    assert isinstance(status.variables["linhas_conta_refs"], list)
    assert status.variables["numero_lote_tiss"] == "L1"


async def test_fake_correlate_message_records_calls() -> None:
    fake = FakeCibSevenTransport()
    await fake.correlate_message("msg.auth.docs_received", "AUTH-amh-1", {"x": 1})
    assert fake.correlate_calls == [
        {
            "message_name": "msg.auth.docs_received",
            "business_key": "AUTH-amh-1",
            "variables": {"x": 1},
            "correlation_keys": {},
            "all_matching": False,
        }
    ]


# ---------------------------------------------------------------------------
# start_process_idempotent — the ONE call site agent graphs should use
# ---------------------------------------------------------------------------


async def test_start_process_idempotent_starts_when_no_active_instance() -> None:
    fake = FakeCibSevenTransport()
    inst = await start_process_idempotent(
        fake,
        process_key="SP-OP-ESCALATION-001",
        business_key="ESC-amh-1",
        variables={},
        audit_sink=FakeStartAuditSink(),
        provenance=_PROV,
    )
    assert inst.already_existed is False
    assert inst.instance_id == "fake-ESC-amh-1"


async def test_start_process_idempotent_returns_existing_never_double_starts() -> None:
    fake = FakeCibSevenTransport()
    fake.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-ESCALATION-001",
            business_key="ESC-amh-1",
            state="ACTIVE",
            already_existed=True,
        )
    )
    inst = await start_process_idempotent(
        fake,
        process_key="SP-OP-ESCALATION-001",
        business_key="ESC-amh-1",
        variables={"x": 1},
        audit_sink=FakeStartAuditSink(),
        provenance=_PROV,
    )
    assert inst.instance_id == "existing-1"
    assert inst.already_existed is True


# ---------------------------------------------------------------------------
# CibSevenHttpTransport — wire format (mocked httpx client, never a real engine here)
# ---------------------------------------------------------------------------


async def test_http_find_active_instance_queries_business_key_and_active_true() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response([{"id": "proc-1", "processDefinitionKey": "SP-OP-ESCALATION-001"}])
    )

    inst = await transport.find_active_instance("ESC-amh-1")

    assert inst is not None
    assert inst.instance_id == "proc-1"
    assert inst.already_existed is True
    call_args = transport._client.get.call_args
    assert call_args[0][0] == "/process-instance"
    assert call_args[1]["params"] == {"businessKey": "ESC-amh-1", "active": "true"}


async def test_http_find_active_instance_returns_none_when_empty() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(return_value=_mock_response([]))  # type: ignore[method-assign]
    assert await transport.find_active_instance("ESC-amh-1") is None


# ---------------------------------------------------------------------------
# `find_any_instance` over a MULTI-ROW history (GK MAJOR-1)
#
# History is a LIST, and since GAP-D3-02 a business key legitimately has more than one row in it:
# an `EXCLUSIVE` key falls through on a finished generation and starts the next one, so
# `[gen-1 COMPLETED, gen-2 ACTIVE]` is the steady state, not an edge case. The previous revision
# returned on the FIRST matching row, so on exactly that history it answered COMPLETED — and the
# gate then read "the claimed generation ended", fell through, and started a SECOND CONCURRENT
# instance. The gate-level consequence is pinned in `test_start_process_dedup_gate.py`; this is
# the wire-level read that has to be right for that fix to hold.
# ---------------------------------------------------------------------------


async def test_http_find_any_instance_prefers_the_live_row_over_an_earlier_finished_one() -> None:
    """THE GK MAJOR-1 PROBE, verbatim: history `[gen-1 COMPLETED, gen-2 ACTIVE]` must answer with
    the LIVE row. Returning the finished one is what let a second concurrent instance start."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [
                {"id": "pi-gen1", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "COMPLETED"},
                {"id": "pi-gen2", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "ACTIVE"},
            ]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.instance_id == "pi-gen2", "the ACTIVE-rescue branch was handed an arbitrary row"
    assert inst.state == "ACTIVE"
    assert inst.already_existed is True
    call_args = transport._client.get.call_args
    assert call_args[0][0] == "/history/process-instance"
    assert call_args[1]["params"] == {"processInstanceBusinessKey": "CANCEL-amh-C-001"}


async def test_http_find_any_instance_returns_a_finished_row_only_when_none_is_live() -> None:
    """The other half: with NO live row anywhere the first finished row is the honest answer —
    that is the evidence `EXCLUSIVE` needs to release the key's next legitimate case."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [
                {"id": "pi-gen1", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "COMPLETED"},
                {
                    "id": "pi-gen2",
                    "processDefinitionKey": "SP-OP-CANCEL-001",
                    "state": "EXTERNALLY_TERMINATED",
                },
            ]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.instance_id == "pi-gen1"
    assert inst.state == "COMPLETED"


async def test_http_find_any_instance_scans_past_a_foreign_definition_to_reach_the_live_row() -> None:
    """The `process_key` filter and the live-row scan compose: a foreign definition sharing the
    business key is skipped, and the scan CONTINUES rather than stopping at the first survivor."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [
                {"id": "pi-foreign", "processDefinitionKey": "SP-OP-CONTAS-001", "state": "ACTIVE"},
                {"id": "pi-gen1", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "COMPLETED"},
                {"id": "pi-gen2", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "ACTIVE"},
            ]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.instance_id == "pi-gen2"


async def test_http_find_any_instance_treats_a_suspended_row_as_live() -> None:
    """A SUSPENDED instance has NOT ended — treating it as finished would let `EXCLUSIVE` start a
    second instance alongside it. Over-matching here refuses a start; under-matching permits a
    duplicate one, and only one of those two errors is recoverable."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [
                {"id": "pi-gen1", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "COMPLETED"},
                {"id": "pi-gen2", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "SUSPENDED"},
            ]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.instance_id == "pi-gen2"
    assert inst.state == "SUSPENDED", "the engine's own state token is passed through verbatim"


async def test_http_find_any_instance_treats_an_unrecognised_open_row_as_live() -> None:
    """An engine build reporting a state token this module has never seen, with no `endTime`, is
    NOT proof the instance ended. Fail toward refusing the start."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [{"id": "pi-odd", "processDefinitionKey": "SP-OP-CANCEL-001", "state": "MIGRATING"}]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.state == "MIGRATING"


async def test_http_find_any_instance_reads_end_time_when_the_state_token_is_missing() -> None:
    """With no `state` at all the engine's `endTime` is the remaining statement about liveness: a
    row that carries one has ended, and `EXCLUSIVE` may release the key's next case on it."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(
            [
                {
                    "id": "pi-ended",
                    "processDefinitionKey": "SP-OP-CANCEL-001",
                    "endTime": "2026-09-03T10:00:00.000+0000",
                }
            ]
        )
    )

    inst = await transport.find_any_instance("CANCEL-amh-C-001", process_key="SP-OP-CANCEL-001")

    assert inst is not None
    assert inst.state == "UNKNOWN"
    assert historic_instance_is_live(inst.state, end_time="2026-09-03T10:00:00.000+0000") is False
    # ...and with no endTime either, the same row is treated as LIVE (refuse, never guess).
    assert historic_instance_is_live("UNKNOWN") is True


async def test_http_start_process_instance_posts_business_key_and_typed_variables() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response({"id": "proc-2", "state": "ACTIVE"})
    )

    inst = await transport.start_process_instance(
        "SP-OP-ESCALATION-001", "ESC-amh-1", {"severidade": "grave", "count": 3, "flag": True}
    )

    assert inst.instance_id == "proc-2"
    assert inst.already_existed is False
    call_args = transport._client.post.call_args
    assert call_args[0][0] == "/process-definition/key/SP-OP-ESCALATION-001/start"
    payload = call_args[1]["json"]
    assert payload["businessKey"] == "ESC-amh-1"
    assert payload["variables"]["severidade"] == {"value": "grave", "type": "String"}
    assert payload["variables"]["count"] == {"value": 3, "type": "Integer"}
    assert payload["variables"]["flag"] == {"value": True, "type": "Boolean"}


async def test_http_start_process_instance_long_typed_above_int32() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(return_value=_mock_response({"id": "proc-3"}))  # type: ignore[method-assign]

    huge = 5_000_000_000  # exceeds java.lang.Integer max
    await transport.start_process_instance("SP-OP-PAGTO-001", "PAG-amh-1", {"valor_pagamento_cents": huge})

    payload = transport._client.post.call_args[1]["json"]
    assert payload["variables"]["valor_pagamento_cents"] == {"value": huge, "type": "Long"}


async def test_http_start_process_instance_dict_and_list_variables_round_trip_as_json() -> None:
    """Symmetric outbound path check (T1.1 task item 2): `_to_camunda_vars` already serializes
    dict/list process variables as `{"value": json.dumps(v), "type": "Json"}` — the exact wire
    counterpart the inbound `_from_camunda_var` fix (this PR) now decodes on read. Confirms the
    outbound half of the contract was already correct (not a second defect) by round-tripping
    the emitted `value` string back through `json.loads`."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(return_value=_mock_response({"id": "proc-4"}))  # type: ignore[method-assign]

    linhas = [{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]
    dossie = {"a": 1, "b": [1, 2, 3]}
    await transport.start_process_instance(
        "SP-OP-CONTAS-001",
        "CONTAS-amh-1",
        {"linhas_conta_refs": linhas, "dossie": dossie},
    )

    payload = transport._client.post.call_args[1]["json"]
    linhas_var = payload["variables"]["linhas_conta_refs"]
    dossie_var = payload["variables"]["dossie"]
    assert linhas_var["type"] == "Json"
    assert dossie_var["type"] == "Json"
    assert json.loads(linhas_var["value"]) == linhas
    assert json.loads(dossie_var["value"]) == dossie


async def test_http_start_process_instance_raises_cibseven_error_on_http_failure() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response({"error": "boom"}, status_code=500)
    )
    with pytest.raises(CibSevenError):
        await transport.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {})


async def test_http_start_process_instance_raises_cibseven_error_on_unreachable() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))  # type: ignore[method-assign]
    with pytest.raises(CibSevenError):
        await transport.start_process_instance("SP-OP-ESCALATION-001", "ESC-amh-1", {})


async def test_http_correlate_message_posts_message_endpoint() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.post = AsyncMock(return_value=_mock_response({}))  # type: ignore[method-assign]

    await transport.correlate_message("msg.auth.docs_received", "AUTH-amh-1", {"a": 1})

    call_args = transport._client.post.call_args
    assert call_args[0][0] == "/message"
    payload = call_args[1]["json"]
    assert payload["messageName"] == "msg.auth.docs_received"
    assert payload["businessKey"] == "AUTH-amh-1"


# ---------------------------------------------------------------------------
# get_process_status — Json-typed variable decode (T1.1 sibling of the harness fix, PR #75)
# ---------------------------------------------------------------------------


def _route_process_status_gets(
    routes: dict[str, Any], *, calls: list[tuple[str, dict[str, Any] | None]] | None = None
) -> Any:
    """Build a `side_effect` for `transport._client.get` that dispatches on the request path,
    mirroring the real engine: `find_active_instance` queries `/process-instance` first, then
    `get_process_status` follows up with `/process-instance/{id}/variables`. `calls`, if given,
    records `(url, params)` for every GET so a test can assert on the exact query sent."""

    def _get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        if calls is not None:
            calls.append((url, params))
        if url not in routes:
            raise AssertionError(f"unexpected GET {url}")
        return routes[url]

    return _get


async def test_http_get_process_status_decodes_json_typed_variable() -> None:
    """T1.1 sibling defect (identical shape to the harness fix merged in PR #75, mirrored here
    at the `mcp_cibseven` transport seam): CIB Seven's `/process-instance/{id}/variables`
    returns a `Json`-typed variable's `value` as a JSON STRING — the exact wire shape
    `tools/workers/harness.py`'s `_from_camunda_var` already decodes on the engine->worker
    leg. Before this fix, `get_process_status` extracted variables via a bare `.get("value")`,
    so a list-valued process variable (e.g. SP-OP-CONTAS-001's `linhas_conta_refs`) arrived as
    the raw string `'[{"numero_guia_tiss": "G1", ...}]'` instead of a Python `list` — any
    caller iterating/indexing it would break or silently misbehave."""
    calls: list[tuple[str, dict[str, Any] | None]] = []
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        side_effect=_route_process_status_gets(
            {
                "/process-instance": _mock_response(
                    [{"id": "proc-1", "processDefinitionKey": "SP-OP-CONTAS-001"}]
                ),
                "/process-instance/proc-1/variables": _mock_response(
                    {
                        "linhas_conta_refs": {
                            "value": '[{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]',
                            "type": "Json",
                        },
                        "numero_lote_tiss": {"value": "L1", "type": "String"},
                        "dossie": {"value": '{"a": 1, "b": [1, 2, 3]}', "type": "Json"},
                        "dossie_vazio": {"value": None, "type": "Json"},
                    }
                ),
            },
            calls=calls,
        )
    )

    status = await transport.get_process_status("CONTAS-amh-1")

    assert status.variables["linhas_conta_refs"] == [
        {"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}
    ]
    assert isinstance(status.variables["linhas_conta_refs"], list)  # NOT the raw JSON string
    assert status.variables["numero_lote_tiss"] == "L1"  # non-Json types unaffected
    assert status.variables["dossie"] == {"a": 1, "b": [1, 2, 3]}  # dict-valued Json too
    assert status.variables["dossie_vazio"] is None  # null Json value -> None, never crashes

    # Live-caught (T1.1): `GET /process-instance/{id}/variables` server-side "deserializes" a
    # `Json`-typed value into a useless Jackson `JsonNode` bean reflection UNLESS
    # `deserializeValues=false` is explicitly requested — a DIFFERENT default than
    # `fetchAndLock`'s (harness, PR #75), which already returns the raw JSON string with no
    # flag needed. Without this param the mocked assertions above would pass (they assert
    # against a hand-authored fixture, not the engine's real default shape) while the real
    # engine would silently hand back bean-reflection garbage — this assertion locks in the
    # actual outbound request shape the live engine requires.
    variables_call = next(c for c in calls if c[0] == "/process-instance/proc-1/variables")
    assert variables_call[1] == {"deserializeValues": "false"}


async def test_http_get_process_status_malformed_json_variable_fails_closed() -> None:
    """Fail-closed contract (mirrors `tools/workers/harness.py`'s `fetch_and_lock` decode
    contract, PR #75): malformed JSON inside a `Json`-typed process variable must NEVER be
    handed to the caller as the raw string (silent corruption). `get_process_status` raises a
    coded `CibSevenVariableDecodeError` instead of swallowing it into an empty/partial result."""
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.get = AsyncMock(  # type: ignore[method-assign]
        side_effect=_route_process_status_gets(
            {
                "/process-instance": _mock_response(
                    [{"id": "proc-9", "processDefinitionKey": "SP-OP-ESCALATION-001"}]
                ),
                "/process-instance/proc-9/variables": _mock_response(
                    {"linhas_conta_refs": {"value": "{not-valid-json[", "type": "Json"}}
                ),
            }
        )
    )

    with pytest.raises(CibSevenVariableDecodeError):
        await transport.get_process_status("ESC-amh-9")


async def test_http_close_closes_client() -> None:
    transport = CibSevenHttpTransport("http://engine/engine-rest")
    transport._client.aclose = AsyncMock()  # type: ignore[method-assign]
    await transport.close()
    transport._client.aclose.assert_awaited_once()
