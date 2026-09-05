"""NEW-12 — um BUG num porto injetado NAO pode virar "dossie indisponivel" (audit 2026-09-04).

O DEFEITO, REPRODUZIDO
------------------------
`carolina/graph.py::_build_dossier` construido com um duplo cujo `generate()` NAO aceita o kwarg
`task_kind` que o no passa: o `TypeError` da ligacao de argumentos era ENGOLIDO pelo
`except Exception:` do metodo e o dossie voltava COMPLETO, com `narrativa=""` — a mesma saida que
"o provedor de LLM caiu" produz. (A fixture deste arquivo desloca a deriva para a ARIDADE em vez
do kwarg; ver o docstring de `_DriftedInference` para o porque — a cerca irma CC-12 proibe a
forma historica em `tests/`, e nao ha' motivo para abrir excecao nela.)

O padrao estava em 25 sitios de 9 grafos, e o repositorio ja' tinha uma vitima registrada: o
comentario de `_RecordingInference.generate` em `test_gather_notes_no_raw_exception.py` documenta
uma cerca que ficou INERTE por exatamente este motivo (o prompt nunca era gravado porque o
`TypeError` sumia).

O QUE ESTE ARQUIVO AFIRMA, POR SITIO
--------------------------------------
1. Duplo com ASSINATURA DERIVADA (sem `task_kind`, ou com aridade errada no leitor) -> o
   `TypeError` PROPAGA para fora do no. E' um bug, e um bug tem de quebrar alto.
2. A falha EXTERNA DECLARADA do mesmo porto (`InferenceProviderError` para o provedor de
   inferencia, `ConnectionError` para o leitor) continua degradando exatamente como antes —
   narrativa vazia / nota de lacuna com token de classe, nenhuma excecao.

A afirmacao 2 e' o que impede o conserto de virar "agora tudo estoura": a degradacao e' um
comportamento CONTRATADO destes nos, e continua valendo para a falha que o porto declara.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from maezo.agents.andre.graph import AndreGraph
from maezo.agents.beatriz.graph import BeatrizGraph
from maezo.agents.carolina.graph import CarolinaGraph
from maezo.agents.fernando.graph import FernandoGraph
from maezo.agents.gustavo.graph import GustavoGraph
from maezo.agents.helena.graph import HelenaGraph
from maezo.agents.lucas.graph import LucasGraph
from maezo.agents.marina.graph import MarinaGraph
from maezo.agents.rafael.graph import RafaelGraph
from maezo.agents.valentina.graph import ValentinaGraph
from maezo.runtime.inference import InferenceProvider, InferenceProviderError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _DriftedInference:
    """Duplo que NAO satisfaz o contrato de chamada do porto: `generate()` sem nem o `prompt`.

    POR QUE A ARIDADE, E NAO O `task_kind` QUE A AUDITORIA SONDOU. A forma historica de NEW-12
    e' um duplo com `phi` e SEM `task_kind` (a deriva que CC-12 custou uma vez, reproduzida LIVE
    nesta sessao contra `carolina/graph.py::_build_dossier` na base `87b51a8`). Essa forma NAO
    pode ser escrita como uma `class` aqui: e' exatamente o que a cerca irma
    `tests/unit/runtime/test_inference_fakes_match_protocol.py` existe para REJEITAR em todo
    `tests/`, e escrever a fixture assim obrigaria a abrir uma excecao naquela cerca — pagar com
    a protecao de todo o repositorio pelo conforto de um duplo. A aridade errada prova o MESMO
    invariante (um duplo que nao satisfaz o contrato de chamada do porto levanta `TypeError` na
    LIGACAO dos argumentos, e esse `TypeError` tem de escapar do no) sem tocar naquela cerca.

    O corpo nunca roda: o `TypeError` nasce antes. O `AssertionError` esta la' para o dia em que
    um grafo parar de passar o prompt — nesse dia o corpo passa a rodar e o teste fica vermelho
    por outra razao legitima, em vez de virar um falso verde.
    """

    model_id = "fake-model"

    async def generate(self) -> str:
        raise AssertionError("inalcancavel: a ligacao de argumentos levanta TypeError antes daqui")


class _UnavailableInference:
    """A falha EXTERNA declarada de `InferenceProvider.generate` (`Raises:` do proprio metodo)."""

    model_id = "fake-model"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        raise InferenceProviderError("noop", "provider unavailable (duplo de teste)", retryable=True)


class _DriftedReader:
    """Leitor FHIR com ARIDADE errada — nao satisfaz o Protocol que o grafo declara."""

    async def read_patient(self) -> dict[str, Any]:
        raise AssertionError("inalcancavel: a chamada passa 1 argumento posicional")

    async def read_patient_summary(self) -> dict[str, Any]:
        raise AssertionError("inalcancavel: a chamada passa 1 argumento posicional")

    async def search_coverage(self) -> Any:
        raise AssertionError("inalcancavel: a chamada passa 1 argumento posicional")


class _UnavailableReader:
    """A falha EXTERNA de um leitor FHIR: rede/servidor fora (`ConnectionError` <: `OSError`)."""

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        raise ConnectionError("HAPI FHIR unreachable (duplo de teste)")

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        raise ConnectionError("HAPI FHIR unreachable (duplo de teste)")

    async def search_coverage(self, patient_id: str) -> Any:
        raise ConnectionError("HAPI FHIR unreachable (duplo de teste)")


def _llm(fake: object) -> InferenceProvider:
    return cast(InferenceProvider, fake)


def _seams() -> dict[str, Any]:
    return {
        "dmn": FakeDmnTransport(),
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
    }


# =================================================================================================
# (A) Os 15 sitios que chamam `self._llm.generate`
# =================================================================================================


async def _andre_dossier(fake: object) -> Any:
    graph = AndreGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "flow": "pagto_dossier", "route": "human_review"}
    return await graph._build_dossier(state, route="human_review")


async def _beatriz_dossier(fake: object) -> Any:
    graph = BeatrizGraph(inference=_llm(fake))
    state: Any = {"tenant_id": "amh"}
    return await graph._build_dossier(state)


async def _carolina_dossier(fake: object) -> Any:
    graph = CarolinaGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "prestador_id": "prestador-1"}
    return await graph._build_dossier(state, route="human_review")


async def _fernando_message(fake: object) -> Any:
    graph = FernandoGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "contrato_id": "c-1"}
    return await graph._build_message(state)


async def _fernando_dossier(fake: object) -> Any:
    graph = FernandoGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "contrato_id": "c-1"}
    return await graph._build_dossier(state)


async def _gustavo_dossier(fake: object) -> Any:
    graph = GustavoGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "protocolo_nip": "NIP-1"}
    return await graph._build_dossier(state, route="review_submission")


async def _helena_classify(fake: object) -> Any:
    graph = HelenaGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._classify_llm(state)


async def _helena_respond(fake: object) -> Any:
    graph = HelenaGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._respond_llm(state, "information")


async def _helena_resumo(fake: object) -> Any:
    graph = HelenaGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._resumo_contexto(state, "solicitacao_humano")


async def _lucas_message(fake: object) -> Any:
    graph = LucasGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._build_message(state)


async def _lucas_dossier(fake: object) -> Any:
    graph = LucasGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._build_dossier(state)


async def _lucas_ack(fake: object) -> Any:
    graph = LucasGraph(inference=_llm(fake), whatsapp=cast(Any, object()), **_seams())
    state: Any = {"tenant_id": "amh", "message_body": "ola"}
    return await graph._build_escalation_ack(state)


async def _marina_dossier(fake: object) -> Any:
    graph = MarinaGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "fluxo": "contas"}
    return await graph._build_dossier(state, route="human_review")


async def _rafael_dossier(fake: object) -> Any:
    graph = RafaelGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "route": "human_auditor"}
    return await graph._build_dossier(state)


async def _valentina_dossier(fake: object) -> Any:
    graph = ValentinaGraph(inference=_llm(fake), **_seams())
    state: Any = {"tenant_id": "amh", "programa": "cronicos"}
    return await graph._build_dossier(state, route="human_review")


_LLM_SITES: dict[str, Callable[[object], Awaitable[Any]]] = {
    "andre::_build_dossier": _andre_dossier,
    "beatriz::_build_dossier": _beatriz_dossier,
    "carolina::_build_dossier": _carolina_dossier,
    "fernando::_build_message": _fernando_message,
    "fernando::_build_dossier": _fernando_dossier,
    "gustavo::_build_dossier": _gustavo_dossier,
    "helena::_classify_llm": _helena_classify,
    "helena::_respond_llm": _helena_respond,
    "helena::_resumo_contexto": _helena_resumo,
    "lucas::_build_message": _lucas_message,
    "lucas::_build_dossier": _lucas_dossier,
    "lucas::_build_escalation_ack": _lucas_ack,
    "marina::_build_dossier": _marina_dossier,
    "rafael::_build_dossier": _rafael_dossier,
    "valentina::_build_dossier": _valentina_dossier,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_LLM_SITES), ids=sorted(_LLM_SITES))
async def test_deriva_de_assinatura_do_provedor_propaga_type_error(sitio: str) -> None:
    """NEW-12: um duplo que nao satisfaz o porto NAO vira `narrativa=''` em silencio."""
    with pytest.raises(TypeError, match="generate"):
        await _LLM_SITES[sitio](_DriftedInference())


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_LLM_SITES), ids=sorted(_LLM_SITES))
async def test_falha_declarada_do_provedor_continua_degradando(sitio: str) -> None:
    """A degradacao contratada sobrevive: `InferenceProviderError` nao escapa do no."""
    resultado = await _LLM_SITES[sitio](_UnavailableInference())
    assert resultado is not None


# =================================================================================================
# (B) Os sitios de `gather` que leem o porto FHIR
# =================================================================================================


async def _rafael_gather(reader: object) -> Any:
    graph = RafaelGraph(inference=_llm(_UnavailableInference()), fhir=cast(Any, reader), **_seams())
    state: Any = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "beneficiario_pseudo_id": "pseudo-1",
        "patient_ref": "patient-1",
        "coverage_ref": "coverage-1",
    }
    return await graph.gather(state)


async def _marina_gather(reader: object) -> Any:
    graph = MarinaGraph(inference=_llm(_UnavailableInference()), fhir=cast(Any, reader), **_seams())
    state: Any = {"tenant_id": "amh", "fluxo": "contas", "patient_summary_ref": "patient-1"}
    return await graph.gather(state)


async def _carolina_gather(reader: object) -> Any:
    graph = CarolinaGraph(inference=_llm(_UnavailableInference()), fhir=cast(Any, reader), **_seams())
    state: Any = {
        "tenant_id": "amh",
        "prestador_id": "prestador-1",
        "direcao": "credenciamento",
        "patient_summary_ref": "patient-1",
    }
    return await graph.gather(state)


async def _valentina_gather(reader: object) -> Any:
    graph = ValentinaGraph(inference=_llm(_UnavailableInference()), fhir=cast(Any, reader), **_seams())
    state: Any = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-1",
        "programa": "cronicos",
        "consentimento_ativo": True,
        "patient_summary_ref": "patient-1",
    }
    return await graph.gather(state)


_READER_SITES: dict[str, Callable[[object], Awaitable[Any]]] = {
    "carolina::gather": _carolina_gather,
    "marina::gather": _marina_gather,
    "rafael::gather": _rafael_gather,
    "valentina::gather": _valentina_gather,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_READER_SITES), ids=sorted(_READER_SITES))
async def test_leitor_com_aridade_errada_propaga_type_error(sitio: str) -> None:
    """NEW-12 (familia leitor): um duplo que nao satisfaz o Protocol quebra alto."""
    with pytest.raises(TypeError):
        await _READER_SITES[sitio](_DriftedReader())


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_READER_SITES), ids=sorted(_READER_SITES))
async def test_leitor_indisponivel_continua_virando_nota_de_lacuna(sitio: str) -> None:
    """A degradacao contratada sobrevive: `ConnectionError` vira nota, nunca excecao."""
    resultado = await _READER_SITES[sitio](_UnavailableReader())
    notas = resultado.get("gather_notes") or resultado.get("notes") or []
    assert any("indisponivel" in str(nota) for nota in notas), notas


# =================================================================================================
# (C) Os 4 sitios de envio que CONTINUAM largos por contrato
# =================================================================================================
#
# Estes quatro nao foram estreitados: a superficie de falha declarada de
# `WhatsAppServer.send_message` inclui um `ValueError` cru ("phone_number_id is not configured —
# refusing to send"), e estreitar trocaria um bug engolido por um TURNO DERRUBADO num ambiente mal
# configurado. O que mudou neles e' a clausula de guarda: seguem absorvendo o fornecedor, e NAO
# absorvem mais o bug. Sem esta secao, a allowlist da cerca seria uma promessa sem prova.


class _DriftedSender:
    """Duplo de envio que nao satisfaz `WhatsAppSender.send(self, to_hash, text)` (aridade)."""

    async def send(self) -> dict[str, Any]:
        raise AssertionError("inalcancavel: a ligacao de argumentos levanta TypeError antes daqui")


class _UnavailableSender:
    """A falha externa de um gateway de mensageria: fora do ar."""

    def __init__(self) -> None:
        self.chamadas = 0

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.chamadas += 1
        raise ConnectionError("whatsapp gateway unreachable (duplo de teste)")


def _fernando_notify_state() -> Any:
    return {
        "tenant_id": "amh",
        "contrato_id": "c-1",
        "to_hash": "hash-1",
        "canal": "whatsapp",
        "status_inadimplencia": "PENDENTE_NOTIFICACAO",
    }


def _lucas_ack_state() -> Any:
    return {
        "tenant_id": "amh",
        "message_body": "ola",
        "to_hash": "hash-1",
        "canal": "whatsapp",
        "route": "escalate_human",
        "process_started": True,
    }


async def _fernando_notify(sender: object) -> Any:
    graph = FernandoGraph(inference=_llm(_UnavailableInference()), whatsapp=cast(Any, sender), **_seams())
    return await graph.notify(_fernando_notify_state())


async def _lucas_ack_send(sender: object) -> Any:
    graph = LucasGraph(inference=_llm(_UnavailableInference()), whatsapp=cast(Any, sender), **_seams())
    return await graph.send_escalation_ack(_lucas_ack_state())


_SENDER_SITES: dict[str, Callable[[object], Awaitable[Any]]] = {
    "fernando::notify": _fernando_notify,
    "lucas::send_escalation_ack": _lucas_ack_send,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_SENDER_SITES), ids=sorted(_SENDER_SITES))
async def test_envio_largo_por_contrato_ainda_propaga_erro_de_programacao(sitio: str) -> None:
    """A clausula de guarda e' o unico motivo pelo qual estes quatro deixam o bug passar."""
    with pytest.raises(TypeError, match="send"):
        await _SENDER_SITES[sitio](_DriftedSender())


@pytest.mark.asyncio
@pytest.mark.parametrize("sitio", sorted(_SENDER_SITES), ids=sorted(_SENDER_SITES))
async def test_envio_largo_por_contrato_continua_absorvendo_o_fornecedor(sitio: str) -> None:
    """E o contrato best-effort sobrevive: gateway fora do ar nao levanta, so' anota."""
    sender = _UnavailableSender()
    resultado = await _SENDER_SITES[sitio](sender)
    assert sender.chamadas == 1
    assert resultado
