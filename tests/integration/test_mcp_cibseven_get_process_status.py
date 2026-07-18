"""Live-engine acceptance test for the T1.1 sibling fix (mcp_cibseven transport read seam).

Confirms, against a REAL CIB Seven engine (`docker compose --profile core up`, ADR-0011 —
never a mock in this file), that `CibSevenHttpTransport.get_process_status` decodes a
`Json`-typed process variable back into a Python `list`/`dict` — the exact wire round-trip the
already-merged `tools/workers/harness.py::CibSevenWorkerTransport.fetch_and_lock` fix (#75)
proved for the engine -> worker leg. This file proves the SAME wire contract for the agent ->
engine leg (`mcp_cibseven/transport.py`, ADR-0001/T1.11), end-to-end:

  1. `CibSevenHttpTransport.start_process_instance` writes a `dict`/`list` process variable —
     `_to_camunda_vars`'s existing `Json` branch types it on the wire (already correct before
     this fix, proven here against the real engine rather than a mock).
  2. `CibSevenHttpTransport.get_process_status` reads it back — `_from_camunda_vars` (this fix)
     decodes CIB Seven's `Json`-typed `value` (a JSON STRING on the real engine's REST response,
     confirmed here — not an assumption) back into the SAME Python `list`/`dict`, never a raw
     string.

Deploys a minimal ad hoc test-only BPMN (NOT under `spec/` — constraint 5, mirrors
`conftest.py::echo_process_bpmn`) with a topic NO worker in this process ever drains, so the
instance stays parked ACTIVE at the external task for the lifetime of the test — exactly what
`get_process_status`'s ACTIVE-instance branch needs to exercise the real `GET
/process-instance/{id}/variables` endpoint (the history branch, taken only for a COMPLETED/
non-active instance, never fetches variables and is out of scope here).

If the engine is unreachable, this test SKIPS via the session-scoped
`_skip_if_engine_unreachable` autouse fixture (`conftest.py`) with an explicit, loud reason —
never a silent pass, never a fabricated result (constraint 3).
"""

from __future__ import annotations

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport

from .conftest import RUN_ID, deploy_process

pytestmark = pytest.mark.integration


async def test_get_process_status_decodes_json_typed_variable_against_real_engine(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    process_key = f"t11_it_jsondecode_{RUN_ID}"
    # Deliberately never registered/drained by any worker in this test — the external task stays
    # locked-nowhere/pending forever, which keeps the process instance ACTIVE for the duration of
    # the test (the point: `get_process_status` must hit its ACTIVE-instance branch, which is the
    # ONLY branch that calls `GET /process-instance/{id}/variables` and therefore the ONLY branch
    # this fix touches).
    topic = f"t11.it.jsondecode.{RUN_ID}"
    await deploy_process(
        engine_client, process_key=process_key, topic=topic, deployment_name=f"t11-jsondecode-{RUN_ID}"
    )

    business_key = f"bk-jsondecode-{RUN_ID}"
    linhas_conta_refs = [{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]
    dossie = {"a": 1, "b": [1, 2, 3]}

    transport = CibSevenHttpTransport(engine_base_url)
    try:
        await transport.start_process_instance(
            process_key,
            business_key,
            {
                "linhas_conta_refs": linhas_conta_refs,
                "dossie": dossie,
                "numero_lote_tiss": "L1",  # non-Json type, must round-trip unaffected
            },
        )

        status = await transport.get_process_status(business_key)

        assert status.state == "ACTIVE"
        assert status.variables["linhas_conta_refs"] == linhas_conta_refs
        assert isinstance(status.variables["linhas_conta_refs"], list), (
            "real CIB Seven engine returned the Json-typed `linhas_conta_refs` variable as a "
            f"raw string, not a decoded list — the fix regressed: {status.variables['linhas_conta_refs']!r}"
        )
        assert status.variables["dossie"] == dossie
        assert isinstance(status.variables["dossie"], dict)
        assert status.variables["numero_lote_tiss"] == "L1"
    finally:
        await transport.close()
