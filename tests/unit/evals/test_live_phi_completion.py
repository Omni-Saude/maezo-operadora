"""Engineering-only seams: synthetic config, sockets/DNS denied, never live evidence."""

from __future__ import annotations

import asyncio
import os
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.evals import _live
from tests.evals.conftest import ReplayInferenceProvider
from tests.unit.evals.test_live_eval_interface import CONFIG


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("MAEZO_", "AWS_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    for name, value in CONFIG.items():
        monkeypatch.setenv(name, value)

    def deny(*args, **kwargs):
        raise AssertionError("unit test forbids sockets and DNS")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, deny)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, deny)


@pytest.fixture
def live():
    observer = _live.build_live_inference()
    yield observer
    observer.close()


def install_unit_wire(monkeypatch, live, *, status=200, text="synthetic narrative"):
    """Deliberately injected UNIT seam; no claim of HTTP/SDK/model liveness."""
    original_type_is = _live._type_is
    raw = SimpleNamespace(status_code=status)
    monkeypatch.setattr(
        _live,
        "_type_is",
        lambda value, module, name: (
            (value is raw and (module, name) == ("botocore.awsrequest", "AWSResponse"))
            or original_type_is(value, module, name)
        ),
    )
    monkeypatch.setattr(live, "_original_send", lambda request: raw)

    async def generate(*args, **kwargs):
        await asyncio.to_thread(live._session.send, object())
        return text

    monkeypatch.setattr(live._provider, "generate", generate)


def test_factory_constructs_only_regional_phi_chain_without_network(live):
    assert live.provider_name == "bedrock_br"
    assert live._impl.phi_capable and not live._impl.is_mock
    assert type(live._transport).__name__ == "BedrockBrRegionalTransport"
    assert live.attempts == live.completions == 0
    with pytest.raises(_live.LiveEvalError, match="exactly one"):
        live.verify()


@pytest.mark.parametrize("name", _live._REQUIRED)
def test_missing_configuration_refused_before_construction(monkeypatch, name):
    monkeypatch.delenv(name)
    constructor = AsyncMock()
    monkeypatch.setattr(_live, "InferenceProvider", constructor)
    with pytest.raises(_live.LiveEvalError, match="explicit PHI"):
        _live.build_live_inference()
    constructor.assert_not_called()


@pytest.mark.parametrize("provider", ["anthropic", "bedrock", "noop", "phi_zone_mock", "br_resident"])
def test_incompatible_provider_refused_before_transport(monkeypatch, provider):
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", provider)
    with pytest.raises(_live.LiveEvalError, match="requires bedrock_br"):
        _live.build_live_inference()


@pytest.mark.parametrize(
    "name,value",
    [
        ("MAEZO_INFERENCE_PHI_ZONE_REQUIRED", "false"),
        ("MAEZO_INFERENCE_PHI_ZONE_REQUIRED", "SENSITIVE-invalid-bool"),
        ("MAEZO_INFERENCE_MODEL", "global.synthetic"),
        ("MAEZO_INFERENCE_MODEL", " synthetic"),
        ("MAEZO_PHI_ENDPOINT_URL", "https://unapproved.invalid/private"),
        ("MAEZO_PHI_VENDOR_DPA_REF", " \t\n"),
    ],
)
def test_invalid_configuration_is_static_and_private(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(_live.LiveEvalError) as error:
        _live.build_live_inference()
    assert value not in str(error.value)
    assert "SENSITIVE" not in str(error.value)


async def test_unit_wire_success_crosses_to_thread_and_counts_one(monkeypatch, live):
    install_unit_wire(monkeypatch, live)
    assert await live.generate("private prompt", phi=True) == "synthetic narrative"
    live.verify()
    assert (live.attempts, live.completions, live.failures) == (1, 1, 0)
    assert _live._CALL.get() is None


@pytest.mark.parametrize("status,text", [(401, "synthetic"), (503, "synthetic"), (200, ""), (200, " \t")])
async def test_failed_http_or_empty_completion_cannot_count(monkeypatch, live, status, text):
    install_unit_wire(monkeypatch, live, status=status, text=text)
    with pytest.raises(_live.LiveEvalError):
        await live.generate("private prompt", phi=True)
    with pytest.raises(_live.LiveEvalError):
        live.verify()
    assert live.completions == 0


async def test_success_without_original_http_send_is_rejected(monkeypatch, live):
    monkeypatch.setattr(live._provider, "generate", AsyncMock(return_value="mock response"))
    with pytest.raises(_live.LiveEvalError):
        await live.generate("private prompt", phi=True)
    assert live.completions == 0


async def test_swallowed_auth_exception_fails_after_body_with_safe_diagnostic(monkeypatch, live):
    secret = "SENSITIVE-response-key-prompt"
    monkeypatch.setattr(live._provider, "generate", AsyncMock(side_effect=RuntimeError(secret)))
    try:
        await live.generate(secret, phi=True)
    except Exception:
        dossier = {"narrativa": ""}  # legitimate graph fallback
    assert dossier == {"narrativa": ""}
    with pytest.raises(_live.LiveEvalError) as error:
        live.verify()
    assert secret not in str(error.value)
    assert live.failures == 1


@pytest.mark.parametrize("replacement", [ReplayInferenceProvider([]), SimpleNamespace(is_mock=False)])
def test_mock_replay_facade_rejected(replacement):
    with pytest.raises(_live.LiveEvalError, match="real inference facade"):
        _live.LiveEvalInference(replacement)


@pytest.mark.parametrize("component", ["provider", "transport", "client", "session", "send"])
async def test_late_substitution_cannot_supply_completion(monkeypatch, live, component):
    if component == "provider":
        monkeypatch.setattr(live._provider, "_impl", SimpleNamespace())
    elif component == "transport":
        monkeypatch.setattr(live._impl, "_transport", SimpleNamespace())
    elif component == "client":
        monkeypatch.setattr(live._transport, "_client", SimpleNamespace())
    elif component == "session":
        monkeypatch.setattr(live._client._endpoint, "http_session", SimpleNamespace())
    else:
        monkeypatch.setattr(live._session, "send", lambda request: "fake")
    with pytest.raises(_live.LiveEvalError):
        await live.generate("private prompt", phi=True)
    assert live.completions == 0


async def test_foreign_instance_wire_receipt_does_not_count(monkeypatch, live):
    other = _live.build_live_inference()
    try:
        install_unit_wire(monkeypatch, other)
        await other.generate("synthetic", phi=True)
        other.verify()
        with pytest.raises(_live.LiveEvalError):
            live.verify()
        token = _live._CALL.set(_live._Call(other))
        try:
            with pytest.raises(_live.LiveEvalError, match="foreign"):
                live._send(object())
        finally:
            _live._CALL.reset(token)
    finally:
        other.close()


async def test_unrelated_task_and_late_context_are_rejected(live):
    with pytest.raises(_live.LiveEvalError, match="foreign"):
        await asyncio.to_thread(live._send, object())
    token = _live._CALL.set(_live._Call(live, active=False))
    try:
        with pytest.raises(_live.LiveEvalError, match="inactive"):
            await asyncio.to_thread(live._send, object())
    finally:
        _live._CALL.reset(token)


async def test_general_zone_cannot_be_substituted(monkeypatch, live):
    install_unit_wire(monkeypatch, live)
    with pytest.raises(_live.LiveEvalError):
        await live.generate("synthetic", phi=False)
    assert live.completions == 0


async def test_extra_call_is_not_one_case_completion(monkeypatch, live):
    install_unit_wire(monkeypatch, live)
    await live.generate("synthetic", phi=True)
    await live.generate("synthetic", phi=True)
    with pytest.raises(_live.LiveEvalError):
        live.verify()


@pytest.mark.parametrize("dossier", [None, {}, {"narrativa": ""}, {"narrativa": " \t"}, {"narrativa": 1}])
def test_narrative_output_contract_is_nonvacuous(dossier):
    with pytest.raises(_live.LiveEvalError, match="nonempty dossier"):
        _live.assert_live_narrative(dossier)


def test_nonempty_narrative_still_requires_separate_wire_receipt(live):
    _live.assert_live_narrative({"narrativa": "synthetic narrative"})
    with pytest.raises(_live.LiveEvalError):
        live.verify()


@pytest.mark.parametrize("component", ["transport", "client", "session", "send"])
def test_preexisting_fake_chain_is_rejected(monkeypatch, live, component):
    # Restore the real send first: a second observer may never wrap an observer.
    live._session.send = live._original_send
    if component == "transport":
        monkeypatch.setattr(live._impl, "_transport", SimpleNamespace())
    elif component == "client":
        monkeypatch.setattr(live._transport, "_client", SimpleNamespace())
    elif component == "session":
        monkeypatch.setattr(live._client._endpoint, "http_session", SimpleNamespace())
    else:
        monkeypatch.setattr(live._session, "send", lambda request: "fake")
    with pytest.raises(_live.LiveEvalError, match="real|substituted"):
        _live.LiveEvalInference(live._provider)


async def test_concurrent_instances_keep_call_contexts_separate(monkeypatch, live):
    other = _live.build_live_inference()
    try:
        install_unit_wire(monkeypatch, live)
        install_unit_wire(monkeypatch, other)
        await asyncio.gather(
            live.generate("synthetic first", phi=True),
            other.generate("synthetic second", phi=True),
        )
        live.verify()
        other.verify()
    finally:
        other.close()


def test_public_runner_projection_never_contains_raw_failure(tmp_path):
    from tests.unit.evals.test_live_eval_interface import wrapper

    secret = "SENSITIVE-OUTPUT-EXCEPTION-CANARY"
    body = (
        "@pytest.mark.eval\n@pytest.mark.llm_live\n"
        f"def test_private_failure():\n    print('{secret}')\n    raise RuntimeError('{secret}')\n"
    )
    assert wrapper(tmp_path, "collect", body, CONFIG).returncode == 0
    result = wrapper(tmp_path, "run", body, CONFIG)
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    for name in ("expected.json", "execution.json", "junit.xml", "validation.json"):
        assert secret not in (tmp_path / name).read_text()


def test_strict_body_cannot_borrow_a_fixture_named_like_observer(tmp_path):
    from tests.unit.evals.test_live_eval_interface import wrapper

    body = """
@pytest.fixture
def live_inference():
    class Foreign:
        def verify(self): pass
    return Foreign()
@pytest.mark.eval
@pytest.mark.llm_live
def test_foreign(live_inference): pass
"""
    assert wrapper(tmp_path, "collect", body, CONFIG).returncode == 0
    result = wrapper(tmp_path, "run", body, CONFIG)
    assert result.returncode != 0
    assert "[live-pytest] PASS" not in result.stdout


def _live_bodies():
    from tests.evals import test_classifier_evals as classifier
    from tests.evals import test_dossier_admin_evals as admin
    from tests.evals import test_dossier_adverse_evals as adverse

    cases = [
        pytest.param(classifier.test_helena_eval_tier_b_live, (case,), id=case["id"])
        for case in classifier._HELENA_TIER_B_CASES
    ]
    cases.extend(
        pytest.param(function, (), id=name)
        for module in (admin, adverse)
        for name, function in vars(module).items()
        if name.startswith("test_") and "tier_b_live" in name
    )
    return cases


@pytest.mark.parametrize("body,args", _live_bodies())
async def test_all_eleven_actual_bodies_refuse_swallowed_completion_failure(monkeypatch, live, body, args):
    monkeypatch.setattr(
        live._provider, "generate", AsyncMock(side_effect=RuntimeError("synthetic auth refusal"))
    )
    with pytest.raises((AssertionError, _live.LiveEvalError)):
        await body(*args, live_inference=live)
    assert (live.attempts, live.completions, live.failures) == (1, 0, 1)
    with pytest.raises(_live.LiveEvalError):
        live.verify()


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_strict_public_projection_drops_capture_properties_and_phase_errors(tmp_path, phase):
    from tests.unit.evals.test_live_eval_interface import wrapper

    secret = "SENSITIVE-ARBITRARY-NARRATIVE"
    body = f'''
@pytest.fixture
def private_fixture(request):
    request.node.user_properties.append(("arbitrary", "{secret}"))
    print("{secret}")
    if "{phase}" == "setup": raise RuntimeError("{secret}")
    yield
    if "{phase}" == "teardown": raise RuntimeError("{secret}")
@pytest.mark.eval
@pytest.mark.llm_live
def test_private(private_fixture):
    print("{secret}")
    if "{phase}" == "call": raise RuntimeError("{secret}")
'''
    assert wrapper(tmp_path, "collect", body, CONFIG).returncode == 0
    result = wrapper(tmp_path, "run", body, CONFIG)
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    for name in ("execution.json", "junit.xml", "validation.json"):
        assert secret not in (tmp_path / name).read_text()
