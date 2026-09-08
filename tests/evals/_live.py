"""PHI live-eval fence (ADR-0006/0009/0017); no SDK or credential handling here.

Receipts establish use of the installed real HTTP implementation in a trusted test
process, not cryptographic authenticity against arbitrary Python monkeypatches.
Only counters survive a call. Content, headers and raw exceptions are never receipts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from maezo.runtime.inference import InferenceProvider, InferenceSettings
from maezo.runtime.inference.br_regional import BedrockBrRegionalTransport
from maezo.runtime.inference.br_resident_provider import BrResidentInferenceProvider


class LiveEvalError(RuntimeError):
    """Static, content-free diagnostic safe for pytest's public projection."""


_REQUIRED = (
    "MAEZO_INFERENCE_PROVIDER",
    "MAEZO_INFERENCE_MODEL",
    "MAEZO_INFERENCE_PHI_ZONE_REQUIRED",
    "MAEZO_PHI_ENDPOINT_URL",
    "MAEZO_PHI_VENDOR_DPA_REF",
)
_ENDPOINT = "https://bedrock-runtime.sa-east-1.amazonaws.com"


def validate_live_configuration() -> InferenceSettings:
    """Require an explicit PHI deployment; actual credentials stay in the gateway.

    A nonblank DPA/model reference is configuration, not proof of owner approval.
    No default model, DPA, credential, endpoint or privacy ratification is invented.
    """
    if any(not os.environ.get(name, "").strip() for name in _REQUIRED):
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: explicit PHI configuration required")
    try:
        settings = InferenceSettings()
    except Exception:
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: invalid inference settings") from None
    if settings.provider != "bedrock_br" or not settings.phi_zone_required:
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: PHI requires bedrock_br and PHI zone declaration")
    if settings.model != settings.model.strip() or settings.model.startswith("global."):
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: explicit regional model required")
    if os.environ["MAEZO_PHI_ENDPOINT_URL"].strip() != _ENDPOINT:
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: configured endpoint must match regional transport")
    return settings


def live_configuration_absent() -> bool:
    """Only an entirely unconfigured optional local run may skip; bad config fails."""
    return not any(os.environ.get(name, "") for name in _REQUIRED)


@dataclass
class _Call:
    owner: object
    wire_successes: int = 0
    active: bool = True


_CALL: ContextVar[_Call | None] = ContextVar("maezo_live_eval_call", default=None)


def _type_is(value: Any, module: str, name: str) -> bool:
    cls = type(value)
    return cls.__module__ == module and cls.__name__ == name


class LiveEvalInference:
    """One instance per pytest body; a completion needs a successful original HTTP send.

    Botocore Stubber/after-call hooks alone do not count. asyncio.to_thread carries
    the invocation context to Bedrock's synchronous send. Foreign instances, late
    sends, successful constructors, swallowed errors and empty returns cannot pass.
    """

    def __init__(self, provider: InferenceProvider) -> None:
        self._provider = provider
        self.attempts = 0
        self.completions = 0
        self.failures = 0
        self._closed = False
        if type(provider) is not InferenceProvider:
            raise LiveEvalError("LIVE EVAL INVALID: real inference facade required")
        impl = provider._impl
        if type(impl) is not BrResidentInferenceProvider or impl.is_mock or not impl.phi_capable:
            raise LiveEvalError("LIVE EVAL INVALID: real PHI provider required")
        transport = impl._transport
        if type(transport) is not BedrockBrRegionalTransport:
            raise LiveEvalError("LIVE EVAL INVALID: real regional transport required")
        client = transport._client
        if not _type_is(client, "botocore.client", "BedrockRuntime"):
            raise LiveEvalError("LIVE EVAL INVALID: real regional client required")
        if client.meta.region_name != "sa-east-1" or client.meta.endpoint_url != _ENDPOINT:
            raise LiveEvalError("LIVE EVAL INVALID: regional client mismatch")
        session = client._endpoint.http_session
        if not _type_is(session, "botocore.httpsession", "URLLib3Session"):
            raise LiveEvalError("LIVE EVAL INVALID: real HTTP session required")
        send = session.send
        if (
            getattr(send, "__self__", object()) is not session
            or getattr(send, "__func__", object()) is not getattr(type(session), "send", None)
            or send.__func__.__module__ != "botocore.httpsession"
        ):
            raise LiveEvalError("LIVE EVAL INVALID: substituted HTTP send")
        self._impl = impl
        self._transport = transport
        self._client = client
        self._session = cast(Any, session)
        self._original_send = send
        self._observed_send = self._send
        self._session.send = self._observed_send

    def _send(self, request: Any) -> Any:
        call = _CALL.get()
        if self._closed or call is None or call.owner is not self or not call.active:
            raise LiveEvalError("LIVE EVAL INVALID: foreign or inactive HTTP call")
        # Never inspect headers/body or retain the response. Only the HTTP status
        # of the installed original send is needed, before facade validation.
        response = self._original_send(request)
        if (
            _type_is(response, "botocore.awsrequest", "AWSResponse")
            and type(response.status_code) is int
            and 200 <= response.status_code < 300
        ):
            call.wire_successes += 1
        return response

    @property
    def model_id(self) -> str | None:
        return self._provider.model_id

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    async def generate(self, prompt: str, *, phi: bool = False, **kwargs: Any) -> str:
        self.attempts += 1
        call = _Call(self)
        token = _CALL.set(call)
        try:
            if (
                self._closed
                or phi is not True
                or self._provider._impl is not self._impl
                or self._impl._transport is not self._transport
                or self._transport._client is not self._client
                or self._client._endpoint.http_session is not self._session
                or self._session.send is not self._observed_send
            ):
                raise LiveEvalError("LIVE EVAL INVALID: changed provider or call zone")
            result = await self._provider.generate(prompt, phi=phi, **kwargs)
            if call.wire_successes != 1 or not isinstance(result, str) or not result.strip():
                raise LiveEvalError("LIVE EVAL INVALID: successful nonempty wire completion required")
            self.completions += 1
            return result
        except Exception:
            self.failures += 1
            raise LiveEvalError("LIVE EVAL FAILED: completion unavailable") from None
        finally:
            call.active = False
            _CALL.reset(token)

    def verify(self) -> None:
        # The seven functions / eleven current nodeids each make exactly one LLM
        # call. Keep the expectation explicit; attempts alone are never evidence.
        if self.attempts != 1 or self.completions != 1 or self.failures:
            raise LiveEvalError("LIVE EVAL FAILED: body requires exactly one successful completion")

    def close(self) -> None:
        self._closed = True
        self._session.send = self._original_send
        self._session.close()


def build_live_inference() -> LiveEvalInference:
    settings = validate_live_configuration()
    try:
        provider = InferenceProvider(settings=settings)
        return LiveEvalInference(provider)
    except Exception:
        raise LiveEvalError("LIVE EVAL UNAVAILABLE: regional provider configuration failed") from None


def assert_live_narrative(dossier: Any) -> None:
    narrative = dossier.get("narrativa") if isinstance(dossier, Mapping) else None
    if not isinstance(narrative, str) or not narrative.strip():
        raise LiveEvalError("LIVE EVAL FAILED: nonempty dossier narrative required")
