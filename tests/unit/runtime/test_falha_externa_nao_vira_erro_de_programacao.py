"""§Delta-F1: um CORPO externo ilegivel e' falha de DEPENDENCIA, nunca erro de programacao.

O DEFEITO QUE ESTE ARQUIVO FECHA
----------------------------------
NEW-12 estreitou 25 `except Exception:` dos nos de agente para
`except PROGRAMMING_ERRORS: raise` + `except EXTERNAL_DEPENDENCY_FAILURES:`
(`maezo.runtime.dependency_failures`). `EXTERNAL_DEPENDENCY_FAILURES` e'
`(RuntimeError, OSError, httpx.HTTPError)` e deixa `ValueError` de fora DE PROPOSITO — e' a
classe de bug mais comum que um duplo de teste levanta. O paragrafo que justificava isso dizia
que "nenhum porto injetado declara `ValueError` como falha de dependencia", e essa premissa era
FALSA em dois pontos, os dois no caminho de producao:

* `tools/mcp_fhir/server.py::FhirServer.{read_resource,search_resources}` terminava em
  `response.json()` sem guarda. `raise_for_status()` converte 4xx/5xx em `httpx.HTTPStatusError`
  (absorvido), mas um **200 com corpo que nao e' JSON** — proxy/WAF respondendo pelo backend,
  payload truncado, `base_url` apontando para um endpoint que nao e' FHIR — chega ao `.json()` e
  levanta `json.JSONDecodeError`, subclasse de `ValueError`. Depois do estreitamento isso deixou
  de degradar (nota de lacuna) e passou a DERRUBAR o turno, nos 10 sitios de leitura da frota.
* o SDK da Anthropic faz o MESMO `response.json()` sem guarda quando o `content-type` termina em
  `json` (`anthropic/_response.py`), e ainda levanta `APIResponseValidationError` quando o corpo
  nao casa com o schema — que nao e' `APIStatusError` e nao e' subclasse de nenhuma das tres
  bases absorvidas. Os cinco `except` de `AnthropicInferenceProvider.generate` cobriam
  auth/rate-limit/timeout/conexao/status e nao cobriam o corpo.

O CONSERTO, E ONDE ELE MORA
-----------------------------
Na camada que POSSUI o contrato do corpo, nao na clausula `except` de dez grafos: cada seam
converte a falha externa no tipo que o seu proprio `Raises:` declara — `FhirResponseError`
(irma de `FhirAuthError`, `RuntimeError`) e `InferenceProviderError` (`RuntimeError`). Alargar
os nos para `ValueError` teria devolvido o silencio que NEW-12 foi aberto para fechar.

Nada acima do socket e' falso aqui: `httpx.MockTransport` troca so' o transporte, e o
`FhirServer` / o cliente do SDK sao os reais, com a `httpx.Response` real e o `.json()` real.
"""

from __future__ import annotations

import json
from typing import Any, Final

import anthropic
import httpx
import pytest

from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.inference import AnthropicInferenceProvider, InferenceProviderError
from maezo.tools.mcp_fhir.server import (
    FhirAuthError,
    FhirResponseError,
    FhirServer,
    FhirSettings,
)

#: Capturado ANTES de qualquer `monkeypatch`: as fabricas abaixo constroem um cliente REAL com o
#: transporte trocado, e referenciar `httpx.AsyncClient` depois do patch seria recursao infinita.
_ASYNC_CLIENT_REAL: Final[type[httpx.AsyncClient]] = httpx.AsyncClient

#: Corpo que um proxy/WAF devolve no lugar do backend clinico. Nao e' JSON, e o `content-type`
#: MENTE dizendo que e' — que e' precisamente o caso que passa por `raise_for_status()`.
_CORPO_NAO_JSON: Final[bytes] = b"<html><body>502 Bad Gateway</body></html>"

#: Segredo de teste deterministico e de baixa entropia (nunca um valor com forma de credencial).
_CHAVE_FALSA: Final[str] = "sk-ant-" + "t" * 24


def _instala_transporte(
    monkeypatch: pytest.MonkeyPatch,
    resposta: httpx.Response,
) -> None:
    """Faz todo `httpx.AsyncClient()` deste teste falar com um transporte de memoria."""

    def _handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            resposta.status_code,
            content=resposta.content,
            headers=dict(resposta.headers),
        )

    def _fabrica(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _ASYNC_CLIENT_REAL(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fabrica)


def _servidor() -> FhirServer:
    return FhirServer(FhirSettings(base_url="http://fhir.invalido/fhir/omni"))


# =================================================================================================
# (A) O seam FHIR — `FhirServer.{read_resource, search_resources}`
# =================================================================================================


def test_a_falha_declarada_do_leitor_e_absorvivel_e_nao_e_um_bug() -> None:
    """O tipo novo tem de cair DO LADO CERTO das duas tuplas — senao o conserto nao consertou.

    Este e' o fato de MRO que sustenta todo o resto do arquivo: `FhirResponseError` e'
    `RuntimeError`, logo os nos o absorvem como "fornecedor indisponivel"; e nao e' nenhuma das
    classes de bug, logo o `except PROGRAMMING_ERRORS: raise` NAO o intercepta antes.
    """
    assert issubclass(FhirResponseError, EXTERNAL_DEPENDENCY_FAILURES)
    assert issubclass(FhirAuthError, EXTERNAL_DEPENDENCY_FAILURES)
    assert not issubclass(FhirResponseError, PROGRAMMING_ERRORS)
    assert not issubclass(FhirAuthError, PROGRAMMING_ERRORS)
    # E a classe de origem continua sendo a que o defeito descreve: um `ValueError`.
    assert issubclass(json.JSONDecodeError, ValueError)
    assert not issubclass(json.JSONDecodeError, EXTERNAL_DEPENDENCY_FAILURES)


@pytest.mark.asyncio
async def test_read_resource_com_corpo_nao_json_levanta_falha_de_dependencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 + corpo que nao e' JSON: falha do FORNECEDOR, tipada, nunca `json.JSONDecodeError`."""
    _instala_transporte(
        monkeypatch,
        httpx.Response(200, content=_CORPO_NAO_JSON, headers={"content-type": "application/json"}),
    )

    with pytest.raises(FhirResponseError) as capturado:
        await _servidor().read_resource("Patient", "p-1")

    assert isinstance(capturado.value.__cause__, json.JSONDecodeError)
    assert "JSONDecodeError" in str(capturado.value)


@pytest.mark.asyncio
async def test_search_resources_com_corpo_nao_json_levanta_falha_de_dependencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O mesmo para a busca: `search_coverage` do Rafael passa por aqui."""
    _instala_transporte(
        monkeypatch,
        httpx.Response(200, content=_CORPO_NAO_JSON, headers={"content-type": "application/json"}),
    )

    with pytest.raises(FhirResponseError):
        await _servidor().search_resources("Coverage", {"beneficiary": "Patient/p-1"})


@pytest.mark.asyncio
async def test_corpo_json_que_nao_e_objeto_tambem_e_falha_de_dependencia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uma LISTA passa pelo `.json()` e so' quebraria la' na frente, com cara de bug.

    `agents/rafael/adapters.py::search_coverage` faz `bundle.get("entry", [])`: sobre uma lista
    isso e' `AttributeError`, i.e. um erro de PROGRAMACAO — a mesma confusao do §Delta-F1, um
    passo adiante. A anotacao `dict[str, Any]` nao e' verificada em runtime; esta checagem e' o
    que a torna verdadeira.
    """
    _instala_transporte(
        monkeypatch,
        httpx.Response(200, content=b"[1, 2, 3]", headers={"content-type": "application/json"}),
    )

    with pytest.raises(FhirResponseError, match="objeto JSON"):
        await _servidor().search_resources("Coverage", {"beneficiary": "Patient/p-1"})


@pytest.mark.asyncio
async def test_a_mensagem_da_falha_nunca_carrega_o_corpo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um recurso FHIR e' PHI por definicao (ADR-0006): so' tokens limitados na mensagem."""
    corpo = b'{"resourceType": "Patient", "name": [{"family": "SOBRENOME-SENSIVEL"'
    _instala_transporte(
        monkeypatch,
        httpx.Response(200, content=corpo, headers={"content-type": "application/json"}),
    )

    with pytest.raises(FhirResponseError) as capturado:
        await _servidor().read_resource("Patient", "p-1")

    assert "SOBRENOME-SENSIVEL" not in str(capturado.value)


@pytest.mark.asyncio
async def test_corpo_bem_formado_continua_atravessando(monkeypatch: pytest.MonkeyPatch) -> None:
    """NAO-VACUIDADE: a guarda nao pode ter fechado o caminho feliz junto com o defeito."""
    _instala_transporte(
        monkeypatch,
        httpx.Response(
            200,
            content=b'{"resourceType": "Patient", "id": "p-1"}',
            headers={"content-type": "application/json"},
        ),
    )

    recurso = await _servidor().read_resource("Patient", "p-1")

    assert recurso == {"resourceType": "Patient", "id": "p-1"}


# =================================================================================================
# (B) O mesmo defeito um andar acima: o corpo do token do Cognito
# =================================================================================================


def _servidor_autenticado() -> FhirServer:
    return FhirServer(
        FhirSettings(
            base_url="http://fhir.invalido/fhir/omni",
            token_url="http://cognito.invalido/oauth2/token",
            client_id="cliente-de-teste",
            client_secret="segredo-" + "z" * 16,
        )
    )


@pytest.mark.asyncio
async def test_token_sem_access_token_falha_fechado(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 do Cognito sem `access_token`: `KeyError` (BUG) viraria turno derrubado; e' auth."""
    _instala_transporte(
        monkeypatch,
        httpx.Response(
            200,
            content=b'{"token_type": "Bearer"}',
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(FhirAuthError, match="access_token"):
        await _servidor_autenticado().read_resource("Patient", "p-1")


@pytest.mark.asyncio
async def test_expires_in_nao_numerico_falha_fechado(monkeypatch: pytest.MonkeyPatch) -> None:
    """`float("em-breve")` e' `ValueError` sobre entrada EXTERNA — nao um bug deste modulo."""
    _instala_transporte(
        monkeypatch,
        httpx.Response(
            200,
            content=b'{"access_token": "t-' + b"a" * 12 + b'", "expires_in": "em-breve"}',
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(FhirAuthError, match="expires_in"):
        await _servidor_autenticado().read_resource("Patient", "p-1")


# =================================================================================================
# (C) O seam do LLM — `AnthropicInferenceProvider.generate`
# =================================================================================================


def _provedor_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    resposta: httpx.Response,
) -> AnthropicInferenceProvider:
    """Provedor REAL com o cliente REAL do SDK; so' o transporte e' de memoria."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", _CHAVE_FALSA)
    provedor = AnthropicInferenceProvider()

    def _handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            resposta.status_code,
            content=resposta.content,
            headers=dict(resposta.headers),
        )

    provedor._client = anthropic.AsyncAnthropic(
        api_key=_CHAVE_FALSA,
        http_client=_ASYNC_CLIENT_REAL(transport=httpx.MockTransport(_handler)),
        max_retries=0,
    )
    return provedor


@pytest.mark.asyncio
async def test_corpo_nao_json_do_provedor_vira_erro_declarado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O SDK faz `response.json()` sem guarda: o `ValueError` nao pode escapar deste modulo."""
    provedor = _provedor_anthropic(
        monkeypatch,
        httpx.Response(200, content=_CORPO_NAO_JSON, headers={"content-type": "application/json"}),
    )

    with pytest.raises(InferenceProviderError) as capturado:
        await provedor.generate("oi")

    assert isinstance(capturado.value.__cause__, ValueError)
    assert issubclass(type(capturado.value), EXTERNAL_DEPENDENCY_FAILURES)


@pytest.mark.asyncio
async def test_corpo_json_fora_do_schema_vira_erro_declarado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Um 200 cujo JSON e' valido mas nao tem a forma de `Message` tambem derrubava o turno.

    MEDIDO, nao suposto: o SDK valida de forma NAO-ESTRITA por padrao, entao ele NAO levanta
    `APIResponseValidationError` aqui — constroi uma `Message` com `content=None`, e o
    `for block in response.content` do provedor levantava
    `TypeError: 'NoneType' object is not iterable`, que esta em `PROGRAMMING_ERRORS`.
    """
    provedor = _provedor_anthropic(
        monkeypatch,
        httpx.Response(
            200,
            content=b'{"nao_e": "uma Message"}',
            headers={"content-type": "application/json"},
        ),
    )

    with pytest.raises(InferenceProviderError):
        await provedor.generate("oi")


@pytest.mark.asyncio
async def test_corpo_text_plain_do_provedor_vira_erro_declarado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O terceiro caminho: com `content-type` nao-JSON o SDK devolve o TEXTO CRU, nao `Message`.

    Com validacao NAO-ESTRITA (o padrao), o SDK nao levanta nada aqui — ele devolve uma `str`, e
    o `response.stop_reason` do provedor virava `AttributeError` sobre ela, i.e. uma classe de
    `PROGRAMMING_ERRORS` derrubando o turno por causa de um proxy que respondeu `text/plain`.
    """
    provedor = _provedor_anthropic(
        monkeypatch,
        httpx.Response(200, content=b"502 Bad Gateway", headers={"content-type": "text/plain"}),
    )

    with pytest.raises(InferenceProviderError, match="fora do contrato"):
        await provedor.generate("oi")


@pytest.mark.asyncio
async def test_erro_do_sdk_fora_das_cinco_clausulas_e_absorvivel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metade `anthropic.AnthropicError` da clausula nao e' decorativa.

    `APIResponseValidationError` (o que o SDK levanta com validacao ESTRITA ligada) nao e'
    `APIStatusError`, entao nao casava com nenhum dos cinco `except`, e nao e' subclasse de
    `RuntimeError`, `OSError` nem `httpx.HTTPError`, entao tambem nao era absorvida pelos nos:
    escapava crua e derrubava o turno. Este teste pina o FATO de MRO que torna a clausula
    necessaria, junto com o de que o tipo declarado do provedor cai do lado absorvivel.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", _CHAVE_FALSA)
    assert issubclass(anthropic.APIResponseValidationError, anthropic.AnthropicError)
    assert not issubclass(anthropic.APIResponseValidationError, anthropic.APIStatusError)
    assert not issubclass(anthropic.APIResponseValidationError, EXTERNAL_DEPENDENCY_FAILURES)
    assert issubclass(InferenceProviderError, EXTERNAL_DEPENDENCY_FAILURES)


@pytest.mark.asyncio
async def test_resposta_bem_formada_do_provedor_continua_atravessando(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NAO-VACUIDADE do lado do LLM: a clausula nova nao engoliu o caminho feliz."""
    mensagem = {
        "id": "msg-1",
        "type": "message",
        "role": "assistant",
        "model": "claude-teste",
        "content": [{"type": "text", "text": "narrativa"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    provedor = _provedor_anthropic(
        monkeypatch,
        httpx.Response(
            200,
            content=json.dumps(mensagem).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
    )

    assert await provedor.generate("oi") == "narrativa"
