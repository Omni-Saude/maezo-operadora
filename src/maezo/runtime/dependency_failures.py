"""Vocabulario FECHADO de "falha de dependencia externa" nos nos dos grafos (NEW-12 / REG-03).

O DEFEITO QUE ESTE MODULO FECHA
--------------------------------
Todo no de agente que fala com uma dependencia INJETADA (`self._llm.generate`,
`self._fhir.read_patient*`/`search_coverage`, `self._population.*`) protegia a chamada com um
`except Exception:` largo e degradava para "dossie minimo" / "nota de lacuna". Isso trata um ERRO
DE PROGRAMACAO como se fosse uma indisponibilidade do fornecedor.

Prova LIVE na base `87b51a8`, `carolina/graph.py::_build_dossier`: o grafo foi construido com um
duplo cujo `generate()` NAO aceita o kwarg `task_kind` que o no passa — exatamente a classe de
deriva de assinatura que CC-12 ja custou uma vez — e o `TypeError` resultante foi ENGOLIDO: o
metodo devolveu um dossie COMPLETO com `narrativa=""`, indistinguivel de "o provedor de LLM
caiu". Nenhum sinal, nenhuma metrica, nenhuma diferenca observavel entre o bug e a
indisponibilidade. O mesmo `except Exception:` estava em 25 sitios de 9 grafos.

O PADRAO (duas clausulas, SEMPRE nesta ordem)
----------------------------------------------
::

    try:
        narrativa = await self._llm.generate(...)
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES:
        narrativa = ""

A primeira clausula existe porque DUAS das classes de bug (`NotImplementedError`,
`RecursionError`) sao subclasses de `RuntimeError` e, sem ela, cairiam dentro da segunda. As
outras nao precisariam dela para propagar (`TypeError`/`AttributeError`/`KeyError`/`IndexError`/
`NameError`/`AssertionError` nao sao subclasses de nada em
:data:`EXTERNAL_DEPENDENCY_FAILURES`); estao enumeradas assim mesmo para que a lista do que um no
NUNCA pode absorver seja legivel em UM lugar, e para que a cerca
(`tests/unit/runtime/test_broad_except_allowlist.py`) possa exigir a clausula por AST em todo
sitio de `src/maezo/agents/*/graph.py` — incluindo os quatro que continuam largos por contrato.

POR QUE CLASSES-BASE E NAO OS NOMES CONCRETOS
-----------------------------------------------
O bound mais apertado que este modulo pode NOMEAR nao e' o mais apertado que existe, e a razao e'
uma cerca, nao uma preguica: `scripts/ci/check_effect_chokepoint_fence.py::_WHOLE_MODULE_FENCED`
proibe importar QUALQUER nome de `maezo.tools.mcp_fhir.server` / `maezo.tools.mcp_whatsapp.server`
fora de dez arquivos sancionados (nenhum deles em `runtime/` ou `agents/*/graph.py`). Logo
`FhirAuthError` nao pode ser citado aqui. O comentario daquela cerca ainda registra a premissa que
ESTE modulo derruba — "no graph catches an mcp_whatsapp/mcp_fhir-specific error type (both catch
bare `Exception`)" —, entao a saida honesta e' declarar a BASE de cada falha declarada e listar,
em comentario, os nomes concretos que ela cobre. `RuntimeError` e' bound de verdade: os tipos de
falha declarados dos seis portos injetados sao TODOS subclasse dele ou de `OSError`, e nenhuma
das classes de bug acima e' subclasse de qualquer um dos dois (salvo as duas re-levantadas pela
primeira clausula).

O QUE ESTE MODULO NAO E'
--------------------------
Nao e' o vocabulario de LABEL `error_type` (`platform/error_types.py`, ALERT-COUNTER-LABELS /
R-063), que mapeia uma excecao para um token BOUNDED de Prometheus e por isso e' um modulo FOLHA
sem nenhum import de `maezo`. Aqui o produto e' o conjunto de CLASSES que um `except` pode
absorver; la' e' o token que a metrica pode exibir. Os dois se cruzam de proposito em
`TypeError`/`KeyError`/`ValueError`, que la' viram `"validacao"` e aqui NUNCA sao absorvidos.
"""

from __future__ import annotations

from typing import Final

import httpx

from maezo.runtime.inference import (
    BrEndpointNotApprovedError,
    InferenceProviderError,
    PhiZoneRoutingError,
)

#: Erros de PROGRAMACAO. Nunca degradam um no: sao RE-LEVANTADOS antes de qualquer tratamento de
#: falha externa (ver o padrao no docstring do modulo). Um `TypeError` aqui e' uma assinatura que
#: derivou (NEW-12), um `AttributeError` e' um porto que nao satisfaz o Protocol, um `KeyError` e'
#: um contrato de estado violado, um `AssertionError` e' um duplo de teste dizendo "este caminho
#: nunca deve ser alcancado" — nenhum deles e' "o fornecedor caiu", e todos precisam quebrar o
#: turno alto para que alguem os veja.
#:
#: `NotImplementedError` e `RecursionError` sao os DOIS itens LOAD-BEARING desta tupla: ambos sao
#: subclasses de `RuntimeError` e portanto seriam absorvidos por `EXTERNAL_DEPENDENCY_FAILURES`
#: sem esta clausula. Um porto stub que ainda levanta `NotImplementedError` e' uma implementacao
#: FALTANDO, nao um fornecedor indisponivel.
PROGRAMMING_ERRORS: Final[tuple[type[Exception], ...]] = (
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    NameError,
    AssertionError,
    NotImplementedError,
    RecursionError,
)

#: Falhas de DEPENDENCIA EXTERNA que um no de agente pode legitimamente degradar (dossie minimo,
#: nota de lacuna com token de classe). Cada base cobre os tipos DECLARADOS dos portos injetados:
#:
#: * `RuntimeError`
#:     - `runtime.inference.errors.InferenceProviderError` (e sua subclasse
#:       `BrRegionalTransportUnavailableError`) — o `Raises:` declarado de
#:       `InferenceProvider.generate`;
#:     - `gateway.seams._base.EffectDeniedError` e as quatro subclasses de forma
#:       (`LacunaDeclaradaDeniedError` e' a forma declarada da classe `leitura_phi_clinica`, i.e.
#:       a negacao que o selo `GatedFhirReader` levanta);
#:     - `tools.mcp_fhir.server.FhirAuthError` (401/403 do servidor FHIR) — nao nomeavel aqui,
#:       ver a nota de cerca no docstring do modulo;
#:     - `tools.mcp_cibseven.transport.CibSevenError` e `tools.workers.dmn_transport.
#:       DmnEvaluationError`, para os portos que um no venha a ler por este mesmo padrao.
#: * `OSError` — falha de rede/socket crua (`ConnectionError`, `TimeoutError`, DNS) E as duas
#:   recusas estruturais de zona, que sao `PermissionError` (subclasse de `OSError`):
#:   `PhiZoneRoutingError` (I-6) e `BrEndpointNotApprovedError` (escape de residencia BR).
#:   As duas seguem sendo ABSORVIDAS aqui de proposito — e' o comportamento que
#:   `rafael/graph.py::_build_dossier` documenta em prosa ("com `phi=True` e um provedor de zona
#:   geral, `PhiZoneRoutingError` cai nele e a narrativa vira '' EM SILENCIO"); mudar isso seria
#:   uma decisao de rota, nao um conserto de `except`, e nao pertence a este WP.
#: * `httpx.HTTPError` — a base de `HTTPStatusError`/`ConnectError`/`ReadTimeout` do cliente cru
#:   que `mcp_fhir.server` e `mcp_whatsapp.server` usam por dentro. NAO e' subclasse de
#:   `RuntimeError` nem de `OSError`, entao sem esta terceira entrada um 502 do HAPI FHIR
#:   derrubaria o turno.
#:
#: `ValueError` esta DELIBERADAMENTE FORA: nenhum porto injetado declara `ValueError` como falha
#: de dependencia (o unico `ValueError` do caminho de efeito e' o "credencial nao configurada" do
#: `WhatsAppServer`, e os quatro sitios de envio continuam largos por contrato — ver a allowlist
#: da cerca), enquanto `ValueError` e' a classe de bug mais comum que um duplo pode levantar.
EXTERNAL_DEPENDENCY_FAILURES: Final[tuple[type[Exception], ...]] = (
    RuntimeError,
    OSError,
    httpx.HTTPError,
)

#: Redundancia INTENCIONAL, verificada por mypy: os tres tipos abaixo ja' estao cobertos pelas
#: bases acima (`InferenceProviderError` <: `RuntimeError`; `PhiZoneRoutingError` e
#: `BrEndpointNotApprovedError` <: `PermissionError` <: `OSError`). Sao re-declarados como uma
#: tupla PROPRIA — e nao adicionados a tupla de cima — para que a cobertura continue sendo um
#: FATO CHECAVEL e nao uma leitura de MRO: `test_broad_except_allowlist.py` afirma
#: `issubclass(t, EXTERNAL_DEPENDENCY_FAILURES)` para cada um, entao no dia em que um deles trocar
#: de base (ou o `Raises:` de `InferenceProvider.generate` ganhar um tipo novo) a cerca fica
#: VERMELHA em vez de o dossie voltar a estourar em producao.
DECLARED_INFERENCE_FAILURES: Final[tuple[type[Exception], ...]] = (
    InferenceProviderError,
    PhiZoneRoutingError,
    BrEndpointNotApprovedError,
)
