"""Leituras FHIR TIPADAS sobre o cliente generico — FAIL-CLOSED (VAL-02).

POR QUE ESTE MODULO EXISTE. `server.py::FhirServer` e um cliente HAPI R4 GENERICO:
`read_resource(resource_type, id)` devolve o que vier no corpo de `GET {base}/{tipo}/{id}` e
`search_resources` devolve o que vier de `GET {base}/{tipo}`. Os tres shims de agente
(`agents/{rafael,valentina,marina}/adapters.py`) prometem, no `agent.yaml` e no Protocol do
grafo, algo bem mais estreito: `mcp-fhir.read_patient`/`read_patient_summary` = UM recurso
`Patient`; `mcp-fhir.search_coverage` = um `Bundle` de `Coverage`. Entre a promessa e a
implementacao nao havia nenhuma verificacao — o que o servidor devolvesse com HTTP 200 seguia
direto para `summary_facts`/`patient_facts`, dai para o prompt `phi=True` e para o dossie que o
engine sela.

E ISSO E ALCANCAVEL SEM BUG NENHUM DE CODIGO. O proprio `FhirSettings` documenta que o
isolamento entre empresas E a particao na URL (`.../fhir/omni`) mais o `client_id` mapeado a UMA
empresa: um `base_url` sem particao, um proxy que reescreve caminho, um `OperationOutcome`
devolvido com 200 por um gateway intermediario — todos produzem um corpo JSON que NAO e o
recurso pedido. Entregar esse corpo como "resumo do beneficiario" e pior do que falhar: o
`gather` de cada agente e best-effort e ja sabe degradar para uma nota de lacuna declarada,
enquanto um resumo de forma desconhecida entra no dossie como se fosse fato.

A REGRA. O tipo declarado no corpo (`resourceType`) tem de ser o tipo pedido. Se nao for,
:class:`FhirResourceTypeError` — que os `except Exception` best-effort dos grafos ja capturam e
convertem na nota de lacuna que a classe `leitura_phi_clinica` declara (`SHAPE_LACUNA_DECLARADA`).

SEM PHI NA MENSAGEM (CC-10). A excecao carrega SO tokens limitados: o tipo esperado e — quando
passa pelo saneamento de :func:`_token_de_tipo` — o tipo recebido. Nunca o id do paciente, nunca
o corpo. O `str(exc)` desta excecao pode ser copiado para uma nota de dossie sem vazar nada, e
mesmo assim os grafos so registram `type(exc).__name__`.
"""

from __future__ import annotations

import re
from typing import Any, Final, Protocol

#: Tipos de recurso FHIR que este modulo sabe pedir. Tabela FECHADA de proposito: uma leitura
#: tipada nova entra aqui junto com a funcao que a expoe, nunca por string no call site.
RESOURCE_PATIENT: Final[str] = "Patient"
RESOURCE_COVERAGE: Final[str] = "Coverage"
RESOURCE_BUNDLE: Final[str] = "Bundle"

#: Forma de um nome de tipo FHIR (R4: `[A-Z][A-Za-z]*`). Um `resourceType` vindo do servidor que
#: nao case com isto NAO e ecoado na mensagem — e conteudo arbitrario de terceiro, nao um token.
_TIPO_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]{1,64}$")

_TIPO_ILEGIVEL: Final[str] = "<tipo-ilegivel>"


class FhirResourceTypeError(RuntimeError):
    """O servidor devolveu um recurso de tipo diferente do pedido.

    `RuntimeError` de proposito: e a especie que os `except Exception` best-effort dos grafos ja
    tratam como "leitura indisponivel", entao a recusa cai no caminho DECLARADO de cada agente em
    vez de escapar para cima de um turno de cuidado.
    """


class GenericFhirClient(Protocol):
    """A parte de `server.py::FhirServer` que estas leituras usam."""

    async def read_resource(self, resource_type: str, resource_id: str) -> dict[str, Any]: ...

    async def search_resources(
        self, resource_type: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]: ...


def _token_de_tipo(valor: object) -> str:
    """`valor` como token limitado, ou `<tipo-ilegivel>` — nunca conteudo cru do servidor."""
    if isinstance(valor, str) and _TIPO_RE.match(valor):
        return valor
    return _TIPO_ILEGIVEL


def exigir_tipo(recurso: object, esperado: str) -> dict[str, Any]:
    """`recurso` se ele for um recurso FHIR do tipo `esperado`; senao levanta. FAIL-CLOSED."""
    if not isinstance(recurso, dict):
        raise FhirResourceTypeError(
            f"resposta FHIR nao e um recurso JSON: esperado {esperado}, recebido {type(recurso).__name__}"
        )
    recebido = recurso.get("resourceType")
    if recebido != esperado:
        raise FhirResourceTypeError(
            f"tipo de recurso FHIR inesperado: esperado {esperado}, recebido {_token_de_tipo(recebido)}"
        )
    return recurso


async def read_patient_resource(server: GenericFhirClient, patient_id: str) -> dict[str, Any]:
    """UM recurso `Patient`, ou :class:`FhirResourceTypeError`.

    A leitura que os tres shims chamam de `read_patient`/`read_patient_summary` — nomes
    diferentes (ids de tool diferentes no catalogo, `fhir.read_patient` e
    `fhir.read_patient_summary`, decididos separadamente pelo PEP) para a MESMA leitura fisica
    nesta build. O "resumo composto" do donor (Patient + Observation + Condition) NAO existe
    aqui, e e por isso que o comentario do `agent.yaml` de quem declara
    `mcp-fhir.read_patient_summary` diz exatamente qual e a forma real.
    """
    return exigir_tipo(await server.read_resource(RESOURCE_PATIENT, patient_id), RESOURCE_PATIENT)


async def search_coverage_entries(server: GenericFhirClient, patient_id: str) -> list[Any]:
    """As `entry` do `Bundle` de `Coverage` do beneficiario, ou :class:`FhirResourceTypeError`.

    A verificacao do `Bundle` importa MAIS aqui do que na leitura de Patient: sem ela,
    `bundle.get("entry", [])` sobre um corpo que nao e Bundle devolve `[]` — indistinguivel de
    "beneficiario sem cobertura ativa", que e um FATO administrativo com consequencia clinica no
    dossie de autorizacao. Um erro de infraestrutura nunca pode virar esse fato.
    """
    bundle = await server.search_resources(
        RESOURCE_COVERAGE, {"beneficiary": f"{RESOURCE_PATIENT}/{patient_id}"}
    )
    exigir_tipo(bundle, RESOURCE_BUNDLE)
    entradas = bundle.get("entry", [])
    if not isinstance(entradas, list):
        raise FhirResourceTypeError(f"Bundle FHIR com `entry` de forma inesperada: {type(entradas).__name__}")
    return entradas


__all__ = [
    "RESOURCE_BUNDLE",
    "RESOURCE_COVERAGE",
    "RESOURCE_PATIENT",
    "FhirResourceTypeError",
    "GenericFhirClient",
    "exigir_tipo",
    "read_patient_resource",
    "search_coverage_entries",
]
