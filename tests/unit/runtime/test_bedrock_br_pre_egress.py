"""C: offline dispatch-counter proofs at the actual PHI transport admission seam.

All prompts, model names, references and credentials below are synthetic fixtures.
A syntactically admitted foundation-model resource is not an approved/existing model.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from maezo.runtime.inference.br_regional import (
    HEADER_VENDOR_DPA_REF,
    BedrockBrRegionalTransport,
    BrRegionalRequest,
)
from maezo.runtime.inference.errors import BrRegionalTransportUnavailableError

ENDPOINT = "https://bedrock-runtime.sa-east-1.amazonaws.com"
MODEL = "example.fixture-v1:0"
ARN_PREFIX = "arn:aws:bedrock:sa-east-1::foundation-model/"


class Client:
    def __init__(
        self, *, endpoint: Any = ENDPOINT, region: Any = "sa-east-1", error: Exception | None = None
    ):
        self.meta = SimpleNamespace(endpoint_url=endpoint, region_name=region)
        self.calls: list[dict[str, Any]] = []
        self.error = error
        self.response: dict[str, Any] = {
            "output": {"message": {"content": [{"text": "SYNTHETIC completion"}]}},
            "usage": {"inputTokens": 2, "outputTokens": 3, "cacheReadInputTokens": 1},
            "stopReason": "end_turn",
        }

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def request(**changes: Any) -> BrRegionalRequest:
    return replace(
        BrRegionalRequest(
            endpoint_url=ENDPOINT,
            model=MODEL,
            stable_prefix="SYNTHETIC prefix ",
            variable_suffix="SYNTHETIC suffix",
            max_tokens=42,
            credential="",
            headers={HEADER_VENDOR_DPA_REF: "test-only-operator-reference"},
        ),
        **changes,
    )


async def refused(client: Client, req: BrRegionalRequest) -> None:
    error = None
    try:
        await BedrockBrRegionalTransport(client=client).send(req)
    except Exception as exc:
        error = exc
    # First assertion records the actual dangerous behavior even if legacy parsing
    # subsequently raised. Post-transmission rejection does not meet this contract.
    assert client.calls == [], "SDK dispatch occurred before admission"
    assert isinstance(error, BrRegionalTransportUnavailableError), repr(error)
    assert error.committed is False
    assert error.retryable is False
    assert req.prompt not in str(error)


BAD_MODELS = [
    "",
    "global.anthropic.fixture-v1:0",
    "us.anthropic.fixture-v1:0",
    "eu.anthropic.fixture-v1:0",
    "apac.anthropic.fixture-v1:0",
    "us-gov.anthropic.fixture-v1:0",
    "profileABC123",
    "application-profile",
    "https://example.invalid/model",
    "../example.model",
    " example.model",
    "example.model ",
    "example.model\n",
    "example.\tmodel",
    "example.模型",
    "example_model",
    "example..model",
    "example.model/other",
    "example.model%2Fother",
    "example.model?redirect=elsewhere",
    "example.model#fragment",
    "EXAMPLE.model",
    "arn:aws:bedrock:sa-east-1:123456789012:inference-profile/global.anthropic.fixture-v1:0",
    "arn:aws:bedrock:sa-east-1:123456789012:application-inference-profile/profileABC123",
    "arn:aws:bedrock:sa-east-1:123456789012:prompt-router/fixture",
    "arn:aws:bedrock:sa-east-1:123456789012:default-prompt-router/fixture",
    "arn:aws:bedrock:sa-east-1:123456789012:prompt/1234567890:1",
    "arn:aws:bedrock:sa-east-1:123456789012:custom-model/fixture/123456789012",
    "arn:aws:bedrock:sa-east-1:123456789012:custom-model-deployment/123456789012",
    "arn:aws:bedrock:sa-east-1:123456789012:imported-model/123456789012",
    "arn:aws:bedrock:sa-east-1:123456789012:provisioned-model/123456789012",
    "arn:aws:sagemaker:sa-east-1:123456789012:endpoint/fixture",
    "arn:aws:bedrock:us-east-1::foundation-model/example.model",
    "arn:aws-cn:bedrock:sa-east-1::foundation-model/example.model",
    "arn:aws-us-gov:bedrock:sa-east-1::foundation-model/example.model",
    "arn:aws:bedrock:sa-east-1:123456789012:foundation-model/example.model",
    ARN_PREFIX + "global.anthropic.fixture-v1:0",
    ARN_PREFIX + "us.anthropic.fixture-v1:0",
    ARN_PREFIX + "example.model/../../inference-profile/fixture",
    ARN_PREFIX + "example.model%2F..",
    ARN_PREFIX + "example.model\r\n",
    ARN_PREFIX + "example.model?redirect=elsewhere",
    ARN_PREFIX + "example.model#fragment",
    ARN_PREFIX + "a" * 64 + ".model",
    "a" * 64 + ".model",
    "example." + "a" * 64,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", BAD_MODELS)
async def test_unsafe_model_forms_never_reach_sdk(model: str) -> None:
    await refused(Client(), request(model=model))


BAD_ENDPOINTS = [
    "",
    "http://bedrock-runtime.sa-east-1.amazonaws.com",
    "HTTPS://bedrock-runtime.sa-east-1.amazonaws.com",
    "https://bedrock-runtime.us-east-1.amazonaws.com",
    "https://s3.sa-east-1.amazonaws.com",
    "https://evilbedrock-runtime.sa-east-1.amazonaws.com",
    "https://bedrock-runtime.sa-east-1.amazonaws.com.evil.invalid",
    ENDPOINT + ":443",
    ENDPOINT + ":8443",
    ENDPOINT + ":bad",
    ENDPOINT + "/",
    ENDPOINT + "/invoke",
    ENDPOINT + "/../",
    ENDPOINT + "/%2F",
    ENDPOINT + "?next=https://example.invalid",
    ENDPOINT + "#fragment",
    "https://user@bedrock-runtime.sa-east-1.amazonaws.com",
    "https://user:pass@bedrock-runtime.sa-east-1.amazonaws.com",
    "https://bedrock-runtime.sa-east-1.amazonaws.com@evil.invalid",
    "https://[invalid",
    ENDPOINT + ".",
    "https://bedrock-runtime.sa-east-1.amazonaws.com\\@evil.invalid",
    ENDPOINT + "\r\n",
    " " + ENDPOINT,
    "https://bedrock-runtime.sa-east-1.amaz\tonaws.com",
    "https://BEDROCK-RUNTIME.sa-east-1.amazonaws.com",
    "https://regional.br-sao-paulo.phi.maezo.internal",
    None,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", BAD_ENDPOINTS)
@pytest.mark.parametrize("side", ["request", "resolved"])
async def test_full_endpoint_must_be_admitted_before_dispatch(endpoint: Any, side: str) -> None:
    client = Client(endpoint=endpoint) if side == "resolved" else Client()
    await refused(client, request(endpoint_url=endpoint) if side == "request" else request())


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["", "us-east-1", "sa-east-1 ", "SA-EAST-1", "global", None])
async def test_resolved_region_must_be_admitted_before_dispatch(region: Any) -> None:
    await refused(Client(region=region), request())


@pytest.mark.asyncio
@pytest.mark.parametrize("dpa", ["", " \t ", None, 42])
async def test_absent_operator_contract_never_dispatches(dpa: Any) -> None:
    await refused(Client(), request(headers={HEADER_VENDOR_DPA_REF: dpa}))


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [MODEL, "example.model", "example.model.variant:0:1", ARN_PREFIX + MODEL])
async def test_direct_model_is_bound_to_regional_foundation_arn(model: str) -> None:
    client = Client()
    req = request(model=model)
    response = await BedrockBrRegionalTransport(client=client).send(req)
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent["modelId"] == (model if model.startswith(ARN_PREFIX) else ARN_PREFIX + model)
    assert sent["messages"] == [{"role": "user", "content": [{"text": req.prompt}]}]
    assert sent["inferenceConfig"] == {"maxTokens": 42, "temperature": 0.2}
    assert response.region_evidence_source == "regional_direct_model_contract"
    assert response.endpoint_evidence_source == "sdk_resolved_endpoint"
    assert response.retention_evidence_source == "operator_contract_reference"
    assert response.synthetic is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception_name,retryable",
    [
        ("ThrottlingException", True),
        ("TooManyRequestsException", True),
        ("AccessDeniedException", False),
        ("ReadTimeoutError", False),
    ],
)
async def test_dispatched_failures_remain_committed(exception_name: str, retryable: bool) -> None:
    error_type = type(exception_name, (Exception,), {})
    client = Client(error=error_type("synthetic SDK error"))
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=client).send(request())
    assert len(client.calls) == 1
    assert exc.value.committed is True
    assert exc.value.retryable is retryable


@pytest.mark.asyncio
async def test_client_metadata_is_rechecked_on_each_request() -> None:
    client = Client()
    transport = BedrockBrRegionalTransport(client=client)
    await transport.send(request())
    client.calls.clear()
    client.meta.endpoint_url = "https://bedrock-runtime.us-east-1.amazonaws.com"
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await transport.send(request())
    assert client.calls == []
    assert exc.value.committed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_response_refuses_committed_without_following(status: int) -> None:
    client = Client()
    client.response["ResponseMetadata"] = {
        "HTTPStatusCode": status,
        "HTTPHeaders": {"location": "https://outside.invalid/redirect"},
    }
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=client).send(request())
    assert len(client.calls) == 1
    assert exc.value.committed is True
    assert exc.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"ResponseMetadata": {"HTTPStatusCode": None}},
        {"usage": {"inputTokens": "invalid"}},
        {"output": {"message": []}},
    ],
)
async def test_malformed_response_after_dispatch_is_committed(response: Any) -> None:
    client = Client()
    client.response = response
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=client).send(request())
    assert len(client.calls) == 1
    assert exc.value.committed is True
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_metadata_drift_after_dispatch_is_committed() -> None:
    class MutatingClient(Client):
        def converse(self, **kwargs: Any) -> dict[str, Any]:
            self.meta.region_name = "us-east-1"
            return super().converse(**kwargs)

    client = MutatingClient()
    with pytest.raises(BrRegionalTransportUnavailableError) as exc:
        await BedrockBrRegionalTransport(client=client).send(request())
    assert len(client.calls) == 1
    assert exc.value.committed is True
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_real_botocore_and_urllib3_do_not_follow_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real SDK/HTTP redirect logic with the lowest socket-owning operation replaced.

    No socket opens and no PHI exists: the pool returns a synthetic 307. This catches
    an automatic redirect below converse, which a fake converse client cannot test.
    """
    import io
    import socket

    boto3 = pytest.importorskip("boto3")
    botocore_config = pytest.importorskip("botocore.config")
    urllib3 = pytest.importorskip("urllib3")
    client = boto3.client(
        "bedrock-runtime",
        region_name="sa-east-1",
        endpoint_url=ENDPOINT,
        aws_access_key_id="synthetic-test-only",
        aws_secret_access_key="synthetic-test-only",
        config=botocore_config.Config(retries={"max_attempts": 1, "mode": "standard"}),
    )
    pool = urllib3.HTTPSConnectionPool("bedrock-runtime.sa-east-1.amazonaws.com", port=443)
    attempts = []

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden in offline SDK redirect proof")

    def response(*args: Any, **kwargs: Any) -> Any:
        attempts.append((args, kwargs))
        return urllib3.response.HTTPResponse(
            body=io.BytesIO(b'{"message":"SYNTHETIC redirect"}'),
            status=307,
            headers={"location": "https://outside.invalid/redirect", "content-type": "application/json"},
            preload_content=False,
        )

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(pool, "_make_request", response)
    monkeypatch.setattr(
        client._endpoint.http_session,
        "_get_connection_manager",
        lambda *args: SimpleNamespace(connection_from_url=lambda url: pool),
    )
    try:
        with pytest.raises(BrRegionalTransportUnavailableError) as exc:
            await BedrockBrRegionalTransport(client=client).send(request())
        assert len(attempts) == 1
        assert exc.value.committed is True
        assert exc.value.retryable is False
    finally:
        client.close()
        pool.close()


def test_sdk_timeout_and_retry_configuration_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    boto3 = pytest.importorskip("boto3")
    calls = []

    def client(*args: Any, **kwargs: Any) -> Client:
        calls.append((args, kwargs))
        return Client()

    monkeypatch.setattr(boto3, "client", client)
    BedrockBrRegionalTransport(timeout_s=4.5)
    args, kwargs = calls[0]
    assert args == ("bedrock-runtime",)
    assert kwargs["region_name"] == "sa-east-1"
    assert kwargs["config"].read_timeout == 4.5
    assert kwargs["config"].connect_timeout == 4.5
    assert kwargs["config"].retries == {"max_attempts": 1, "mode": "standard"}
