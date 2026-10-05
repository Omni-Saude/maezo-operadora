"""Preserved independent AW-SOURCE-F01 witness, not a test of repaired source.

Transcribed by ROOT from /root/capability_source_verifier's final response.
Run with the project .venv in an isolated checkout of e91590f1. Synthetic UNIT
data only; no provider, database, engine, credential or operational authority.
Input hashes deliberately refuse running this historical witness on repairs.
"""

import asyncio
import hashlib
import pathlib
import runpy
import warnings
from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.lucas.administrative.graph import compras_consumer
from maezo.agents.lucas.administrative.state import (
    administrative_checkpoint_config,
    new_administrative_state,
)
from maezo.gateway.capabilities.models import CapabilityOutcome, VerifiedFulfillmentFact
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.pseudonymizer import Pseudonymizer

PINS = {
    "src/maezo/agents/lucas/administrative/graph.py":
        "c93acd39a356556e5bac1d11e9d10cc60a2fe29bbe76aed0458176fdfe8f135f",
    "src/maezo/agents/lucas/administrative/state.py":
        "9c4601e768ab1a950a7a9fe3b40868d8125b005892921cde72615eb785eedc33",
    "tests/unit/agents/test_lucas_administrative_graph.py":
        "cae3f38a7b5d903aef293062020a899d3c3d832ef6aa78637020b01291bbc339",
}
for name, digest in PINS.items():
    if hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() != digest:
        raise SystemExit("Historical witness requires the exact e915 source; repair is not baseline")

values = runpy.run_path("tests/unit/agents/test_lucas_administrative_graph.py")["values"]
MARKER = "SYNTHETIC_CLINICAL_BODY_90210"


def contains(value):
    if isinstance(value, str):
        return MARKER in value
    if isinstance(value, bytes):
        return MARKER.encode() in value
    if isinstance(value, dict):
        return any(contains(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains(item) for item in value)
    return False


async def check(kind):
    initial = new_administrative_state(values())
    if kind == "model_copy":
        turn = initial["turn"]
        initial["turn"] = replace(
            turn,
            payload=turn.payload.model_copy(update={"wait_ref": {"clinical": MARKER}}),
        )
    else:
        initial["outcome"] = CapabilityOutcome(
            result=VerifiedFulfillmentFact(
                fact_kind="pending",
                source_fact_ref="unit-fact",
                source_revision="unit-rev",
                clinical_result_context_ref=MARKER,
            )
        )
    saver = InMemorySaver()
    graph = compras_consumer(
        tenant_ref="unit-tenant-a", service=CapabilityService()
    ).build().compile(checkpointer=saver)
    config = administrative_checkpoint_config(
        task_type="journey.compras.step",
        tenant_ref="unit-tenant-a",
        journey_ref="unit-journey-a",
        pseudonymizer=Pseudonymizer(key=b"unit-verifier-key"),
    )
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        result = await graph.ainvoke(initial, config)
    observed = (
        result["technical_status"],
        result["outcome"],
        contains(saver.blobs),
        contains(saver.writes),
        any(MARKER in str(w.message) for w in seen),
    )
    print(kind, observed)
    assert observed[:4] == ("disabled", None, True, True)


async def main():
    await check("model_copy")
    await check("output")


asyncio.run(main())
