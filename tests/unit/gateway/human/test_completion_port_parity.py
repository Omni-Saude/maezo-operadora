"""P-17 parity for the INTERIM completion ports (DL-0049).

`tests/unit/runtime/test_inference_fakes_match_protocol.py` generalised signature parity to a
table of ten Protocol families and declared, in its own docstring, that a shape outside that
table is a COMPLETENESS FAILURE to be registered rather than ignored. `HumanTaskTransport`,
`DirectTaskCompletion` and `CompletionAuditSink` are not in that table — they are ABCs in
`gateway/human`, not Protocols in `runtime`/`a2a`/`tools`, so its scan does not reach them.

This file is that registration, scoped to what DL-0049 added. It applies the SAME comparison the
fence applies (positional names by position, keyword-only names by set, presence of a default,
`*args`/`**kwargs`, sync vs async) and reads the requirement from the REAL port at runtime, never
from a hardcoded signature: a parameter added to a port tomorrow breaks this file immediately.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest
from tests.unit.gateway.human.test_gateway import Transport
from tests.unit.gateway.human.test_task_completion_gateway import (
    CompletingTransport,
    RecordingAudit,
    RecordingCompletion,
)

from maezo.gateway.human.completion import CompletionAuditSink, DirectTaskCompletion
from maezo.gateway.human.engine_reads import EngineHumanTaskTransport
from maezo.gateway.human.ports import HumanTaskTransport


def shape(function: Callable[..., Any]) -> tuple[Any, ...]:
    signature = inspect.signature(function)
    parameters = signature.parameters
    return (
        tuple(
            name
            for name, p in parameters.items()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ),
        frozenset(name for name, p in parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY),
        frozenset(name for name, p in parameters.items() if p.default is not inspect.Parameter.empty),
        tuple(
            p.kind
            for p in parameters.values()
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ),
        inspect.iscoroutinefunction(function),
    )


@pytest.mark.parametrize("method", ["read_task", "complete_task"])
@pytest.mark.parametrize(
    "implementation",
    [
        # The production adapter, the double this package's tests drive the gateway with, and
        # the pre-DL-0049 double that inherits the refusing default (and must keep inheriting it).
        EngineHumanTaskTransport,
        CompletingTransport,
        Transport,
    ],
)
def test_every_task_transport_follows_the_real_port(implementation: type, method: str) -> None:
    assert shape(getattr(implementation, method)) == shape(getattr(HumanTaskTransport, method))


def test_the_completion_capability_double_follows_the_real_port() -> None:
    assert shape(RecordingCompletion.complete) == shape(DirectTaskCompletion.complete)


def test_the_audit_sink_double_follows_the_real_port() -> None:
    assert shape(RecordingAudit.record) == shape(CompletionAuditSink.record)


def test_the_pre_interim_double_still_refuses_by_inheritance() -> None:
    """`Transport` predates DL-0049 and declares no `complete_task`: it MUST stay refusing.

    This is the property that let `complete_task` be added to the port without touching a single
    existing adapter — and the property a future author would silently break by giving the base
    class a working default instead of a refusal.
    """
    assert "complete_task" not in vars(Transport)
    assert Transport.complete_task is HumanTaskTransport.complete_task
