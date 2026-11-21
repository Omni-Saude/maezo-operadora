"""DL-0191: com o dossie de REFERENCIA de cada agente, nenhuma chave some do prompt sem decisao.

O renderizador de fatos descarta a chave fora da lista fechada `permitidos` do sitio (e conta o
descarte em `maezo_prompt_fatos_descartados_total{agente}`). Descarte e' falha FECHADA, mas e'
silencioso para o leitor da narrativa: uma chave nova que um montador acrescentasse a `fatos`
sumiria do prompt sem ninguem decidir se deveria ir. Este arquivo fecha essa porta, por sitio de
montagem e por fluxo:

1. NO RENDERIZADOR: com o estado sintetico de referencia, o que chega ao renderizador cabe
   inteiro na lista do sitio — nenhuma chave e' descartada la' e o contador nao anda.
2. NO MONTADOR: as chaves do dicionario de fatos completo (o que o dossie ou a mensagem devolve)
   menos a lista do sitio sao EXATAMENTE o `DESCARTE_ESPERADO` daquele sitio. Vazio por padrao;
   so' tem algo onde ha decisao registrada: guia e CID-10 do rafael (DL-0190), as chaves de
   vinculo da marina (DL-0191), os identificadores e chaves de vinculo dos outros sete (U1c) e os
   campos de texto livre que gustavo e carolina mandam em bloco NAO CONFIAVEL proprio (HEL-06),
   nunca no repr de fatos.

Chave nova num montador faz (2) falhar ate' alguem decidir: acrescenta-la a lista do sitio (vai
ao prompt) ou ao `DESCARTE_ESPERADO` (fica so' no dossie).

Todos os valores sao SINTETICOS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import structlog

from maezo.platform.observability import get_metrics_collector
from maezo.runtime import prompt_format


class _CapturaPrompt:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return "narrativa sintetica"


def _com_deps(classe: Any, llm: Any, **extra: Any) -> Any:
    return classe(inference=llm, dmn=None, cibseven=None, audit_sink=None, **extra)


#: Estado sintetico comum de referencia: identificadores e campos que os montadores leem, todos
#: com valor (o montador produz todas as chaves de qualquer modo; o valor so' torna o caso
#: representativo).
_COMUM: dict[str, Any] = {
    "tenant_id": "tenant-sintetico",
    "route": "human_review",
    "motivo_humano": "dmn_analise_humana",
    "numero_guia_tiss": "FICT-GUIA-0001",
    "cid10": "Q99.9-SINTETICO",
    "codigo_procedimento_tuss": "10101012",
    "beneficiario_pseudo_id": "pseudo-sintetico-0001",
    "prestador_id": "PREST-SINTETICO-0001",
}

#: Sitio -> chaves de `fatos` que, por DECISAO registrada, ficam fora do repr de fatos do prompt.
DESCARTE_ESPERADO: dict[str, frozenset[str]] = {
    "rafael._build_dossier": frozenset({"numero_guia_tiss", "cid10"}),
    "marina._build_dossier/contas": frozenset({"numero_guia_tiss", "numero_lote_tiss", "numero_conta"}),
    "marina._build_dossier/recurso": frozenset({"numero_guia_tiss", "cid10", "glosa_id"}),
    "marina._build_dossier/reembolso": frozenset(
        {"numero_guia_tiss", "protocolo_reembolso", "relacao_com_titular"}
    ),
    # U1c (ADR-0079 §2): identificadores e chaves de vinculo de cada caso ficam so' no dossie.
    "valentina._build_dossier/stratify": frozenset({"beneficiario_pseudo_id"}),
    "valentina._build_dossier/enroll": frozenset({"beneficiario_pseudo_id"}),
    # HEL-06: texto livre de terceiro vai em bloco NAO CONFIAVEL proprio, nao no repr; U1c: os
    # numeros da NIP na ANS ficam so' no dossie.
    "gustavo._build_dossier/nip": frozenset(
        {"tema_nip", "referencia_negativa_original", "numero_nip_ans", "protocolo_ans"}
    ),
    "beatriz._build_dossier": frozenset(
        {
            "numero_caso",
            "tenant_id",
            "entidade_pseudo_id",
            "prestador_id",
            "beneficiario_pseudo_id",
            "numero_contrato",
            "feature_snapshot_ref",
            "resumo_fhir",
        }
    ),
    "carolina._build_dossier": frozenset({"motivo_informado", "prestador_id", "protocolo_cred"}),
    "andre._build_dossier/pagto_dossier": frozenset({"ordem_pagamento_id"}),
    "andre._build_dossier/adequacao_dossier": frozenset({"ordem_pagamento_id"}),
    "fernando._build_message": frozenset({"numero_contrato"}),
    "fernando._build_dossier": frozenset({"numero_contrato"}),
}


def _sitios() -> list[dict[str, Any]]:
    from maezo.agents.andre.graph import AndreGraph
    from maezo.agents.beatriz.graph import BeatrizGraph
    from maezo.agents.carolina.graph import CarolinaGraph
    from maezo.agents.fernando.graph import FernandoGraph
    from maezo.agents.gustavo.graph import GustavoGraph
    from maezo.agents.lucas.graph import LucasGraph
    from maezo.agents.marina.graph import MarinaGraph
    from maezo.agents.rafael.graph import RafaelGraph
    from maezo.agents.valentina.graph import ValentinaGraph

    async def dossie(grafo: Any, state: dict[str, Any]) -> Any:
        return await grafo._build_dossier(state)

    async def dossie_com_rota(grafo: Any, state: dict[str, Any]) -> Any:
        return await grafo._build_dossier(state, route="human_review")

    async def mensagem(grafo: Any, state: dict[str, Any]) -> Any:
        return await grafo._build_message(state)

    def sitio(
        nome: str, agente: str, constroi: Callable[[Any], Any], monta: Any, extra: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "sitio": nome,
            "agente": agente,
            "constroi": constroi,
            "monta": monta,
            "state": {**_COMUM, **extra},
        }

    return [
        sitio(
            "rafael._build_dossier",
            "rafael",
            lambda llm: _com_deps(RafaelGraph, llm),
            dossie,
            {
                "route": "human_auditor",
                "motivo_auditor": "dmn_analise_humana",
                "carater_atendimento": "eletivo",
                "valor_estimado_brl": 250.0,
            },
        ),
        sitio(
            "marina._build_dossier/contas",
            "marina",
            lambda llm: _com_deps(MarinaGraph, llm),
            dossie_com_rota,
            {"flow": "contas", "numero_lote_tiss": "FICT-LOTE-0001", "numero_conta": "FICT-CONTA-0001"},
        ),
        sitio(
            "marina._build_dossier/recurso",
            "marina",
            lambda llm: _com_deps(MarinaGraph, llm),
            dossie_com_rota,
            {"flow": "recurso", "glosa_id": "FICT-GLOSA-0001"},
        ),
        sitio(
            "marina._build_dossier/reembolso",
            "marina",
            lambda llm: _com_deps(MarinaGraph, llm),
            dossie_com_rota,
            {"flow": "reembolso", "protocolo_reembolso": "FICT-REEMB-0001", "relacao_com_titular": "titular"},
        ),
        sitio(
            "valentina._build_dossier/stratify",
            "valentina",
            lambda llm: _com_deps(ValentinaGraph, llm),
            dossie_com_rota,
            {"task": "stratify"},
        ),
        sitio(
            "valentina._build_dossier/enroll",
            "valentina",
            lambda llm: _com_deps(ValentinaGraph, llm),
            dossie_com_rota,
            {"task": "enroll"},
        ),
        sitio(
            "gustavo._build_dossier/ans_submit",
            "gustavo",
            lambda llm: _com_deps(GustavoGraph, llm),
            dossie_com_rota,
            {"fluxo": "ans_submit"},
        ),
        sitio(
            "gustavo._build_dossier/nip",
            "gustavo",
            lambda llm: _com_deps(GustavoGraph, llm),
            dossie_com_rota,
            {"fluxo": "nip", "tema_nip": "tema sintetico", "referencia_negativa_original": "FICT-NEG-0001"},
        ),
        sitio("beatriz._build_dossier", "beatriz", lambda llm: BeatrizGraph(inference=llm), dossie, {}),
        sitio(
            "carolina._build_dossier",
            "carolina",
            lambda llm: _com_deps(CarolinaGraph, llm),
            dossie_com_rota,
            {"direcao": "descredenciamento", "motivo_informado": "motivo sintetico"},
        ),
        sitio(
            "andre._build_dossier/pagto_dossier",
            "andre",
            lambda llm: _com_deps(AndreGraph, llm),
            dossie_com_rota,
            {"flow": "pagto_dossier"},
        ),
        sitio(
            "andre._build_dossier/adequacao_dossier",
            "andre",
            lambda llm: _com_deps(AndreGraph, llm),
            dossie_com_rota,
            {"flow": "adequacao_dossier"},
        ),
        sitio(
            "fernando._build_message",
            "fernando",
            lambda llm: _com_deps(FernandoGraph, llm, whatsapp=None),
            mensagem,
            {},
        ),
        sitio(
            "fernando._build_dossier",
            "fernando",
            lambda llm: _com_deps(FernandoGraph, llm, whatsapp=None),
            dossie,
            {},
        ),
        sitio(
            "lucas._build_message",
            "lucas",
            lambda llm: _com_deps(LucasGraph, llm, whatsapp=None),
            mensagem,
            {},
        ),
    ]


_SITIOS = _sitios()


def _descartes(agente: str) -> float:
    valor = get_metrics_collector().registry.get_sample_value(
        "maezo_prompt_fatos_descartados_total", {"agente": agente}
    )
    return valor or 0.0


def _fatos_do_resultado(resultado: Any) -> dict[str, Any] | None:
    if isinstance(resultado, tuple):
        resultado = resultado[0]
    if not isinstance(resultado, dict):
        return None
    fatos = resultado.get("fatos", resultado.get("fatos_agregados"))
    return fatos if isinstance(fatos, dict) else None


async def _rodar(
    sitio: dict[str, Any], monkeypatch: pytest.MonkeyPatch, llm: _CapturaPrompt | None = None
) -> tuple[list[tuple[set[str], frozenset[str]]], dict[str, Any] | None]:
    """`llm`: quem quiser ler o prompt passa o seu `_CapturaPrompt` e le' `llm.prompts`."""
    import importlib

    modulo = importlib.import_module(f"maezo.agents.{sitio['agente']}.graph")
    atributo = "render_fatos" if sitio["agente"] == "rafael" else "render_fatos_para_prompt"
    original = prompt_format.render_fatos_para_prompt
    chamadas: list[tuple[set[str], frozenset[str]]] = []

    def _espia(facts: Any, **kw: Any) -> str:
        chamadas.append((set(facts), kw["permitidos"]))
        return original(facts, **kw)

    monkeypatch.setattr(modulo, atributo, _espia)
    if llm is None:
        llm = _CapturaPrompt()
    resultado = await sitio["monta"](sitio["constroi"](llm), dict(sitio["state"]))
    return chamadas, _fatos_do_resultado(resultado)


@pytest.mark.parametrize("sitio", _SITIOS, ids=lambda s: s["sitio"])
async def test_nada_e_descartado_no_renderizador(
    sitio: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    antes = _descartes(sitio["agente"])

    chamadas, _ = await _rodar(sitio, monkeypatch)

    assert len(chamadas) == 1, f"{sitio['sitio']}: esperado 1 render, vieram {len(chamadas)}"
    chaves, permitidos = chamadas[0]
    assert not chaves - permitidos, (
        f"{sitio['sitio']}: descartadas no renderizador {sorted(chaves - permitidos)}"
    )
    assert _descartes(sitio["agente"]) == antes


@pytest.mark.parametrize("sitio", _SITIOS, ids=lambda s: s["sitio"])
async def test_o_que_fica_fora_do_prompt_e_so_o_descarte_esperado(
    sitio: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas, fatos = await _rodar(sitio, monkeypatch)
    chaves, permitidos = chamadas[0]
    completos = set(fatos) if fatos is not None else chaves

    fora = completos - permitidos
    esperado = DESCARTE_ESPERADO.get(sitio["sitio"], frozenset())
    assert fora == esperado, (
        f"{sitio['sitio']}: fora do prompt {sorted(fora)}, esperado {sorted(esperado)}. Chave nova no "
        "montador: acrescente-a a lista do sitio ou ao DESCARTE_ESPERADO, com decisao (DL-0191)."
    )


def test_todo_descarte_esperado_e_de_um_sitio_que_existe() -> None:
    assert set(DESCARTE_ESPERADO) <= {s["sitio"] for s in _SITIOS}


def test_todo_agente_do_vocabulario_tem_sitio_de_referencia() -> None:
    from maezo.platform.observability import PROMPT_FATOS_AGENTES

    assert {s["agente"] for s in _SITIOS} == PROMPT_FATOS_AGENTES


#: Sitio -> (atributo da lista de RETIRADAS do modulo, campos de texto livre que vao em bloco
#: proprio). U1c: o sitio recorta so' o que retira de proposito; retiradas + texto livre =
#: `DESCARTE_ESPERADO`, e nenhuma retirada esta na lista `permitidos` do sitio.
_RETIRADAS_POR_SITIO: dict[str, tuple[str, frozenset[str]]] = {
    "rafael._build_dossier": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "marina._build_dossier/contas": ("_RETIRADAS_DO_PROMPT_CONTAS", frozenset()),
    "marina._build_dossier/recurso": ("_RETIRADAS_DO_PROMPT_RECURSO", frozenset()),
    "marina._build_dossier/reembolso": ("_RETIRADAS_DO_PROMPT_REEMBOLSO", frozenset()),
    "valentina._build_dossier/stratify": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "valentina._build_dossier/enroll": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "gustavo._build_dossier/ans_submit": ("_RETIRADAS_DO_PROMPT_ANS_SUBMIT", frozenset()),
    "gustavo._build_dossier/nip": (
        "_RETIRADAS_DO_PROMPT_NIP",
        frozenset({"tema_nip", "referencia_negativa_original"}),
    ),
    "beatriz._build_dossier": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "carolina._build_dossier": ("_RETIRADAS_DO_PROMPT", frozenset({"motivo_informado"})),
    "andre._build_dossier/pagto_dossier": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "andre._build_dossier/adequacao_dossier": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "fernando._build_message": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "fernando._build_dossier": ("_RETIRADAS_DO_PROMPT", frozenset()),
    "lucas._build_message": ("_RETIRADAS_DO_PROMPT", frozenset()),
}


def test_todo_sitio_declara_as_retiradas() -> None:
    assert set(_RETIRADAS_POR_SITIO) == {s["sitio"] for s in _SITIOS}


@pytest.mark.parametrize("sitio", _SITIOS, ids=lambda s: s["sitio"])
async def test_as_retiradas_do_sitio_sao_o_descarte_esperado(
    sitio: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    modulo = importlib.import_module(f"maezo.agents.{sitio['agente']}.graph")
    atributo, texto_livre = _RETIRADAS_POR_SITIO[sitio["sitio"]]
    retiradas = getattr(modulo, atributo)
    chamadas, _ = await _rodar(sitio, monkeypatch)
    _, permitidos = chamadas[0]

    assert isinstance(retiradas, frozenset)
    assert retiradas | texto_livre == DESCARTE_ESPERADO.get(sitio["sitio"], frozenset())
    assert not retiradas & permitidos


@pytest.mark.parametrize("sitio", _SITIOS, ids=lambda s: s["sitio"])
async def test_chave_nova_num_montador_chega_ao_renderizador_e_e_contada(
    sitio: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """U1c, nao-vacuidade do ALARME: o sitio recorta so' as retiradas, entao uma chave que um
    montador passe a mandar chega ao renderizador, sai do prompt, entra no log de descarte (so' o
    nome) e anda o contador `maezo_prompt_fatos_descartados_total{agente}`. A chave nova e'
    injetada no ponto em que o recorte do sitio a receberia (`sem_chaves_retiradas`)."""
    import importlib

    modulo = importlib.import_module(f"maezo.agents.{sitio['agente']}.graph")
    original = modulo.sem_chaves_retiradas
    sentinela = f"FICT-SENTINELA-{sitio['sitio']}"

    def _com_chave_nova(facts: Any, retiradas: Any) -> dict[str, Any]:
        com_nova: dict[str, Any] = original({**facts, "chave_nova_sintetica": sentinela}, retiradas)
        return com_nova

    monkeypatch.setattr(modulo, "sem_chaves_retiradas", _com_chave_nova)
    antes = _descartes(sitio["agente"])
    llm = _CapturaPrompt()

    with structlog.testing.capture_logs() as logs:
        chamadas, _ = await _rodar(sitio, monkeypatch, llm)

    chaves, permitidos = chamadas[0]
    assert "chave_nova_sintetica" in chaves - permitidos  # chega ao renderizador...
    assert _descartes(sitio["agente"]) == antes + 1  # ...e' contada...
    eventos = [e for e in logs if e["event"] == "prompt_fatos_chaves_fora_da_lista_descartadas"]
    assert [e["chaves"] for e in eventos] == [["chave_nova_sintetica"]]  # ...e registrada pelo nome
    assert sentinela not in repr(logs)
    # ...e nem o nome nem o VALOR chegam ao modelo (o prompt existe: sem ele o teste seria vazio).
    assert len(llm.prompts) == 1, f"{sitio['sitio']}: esperado 1 prompt, vieram {len(llm.prompts)}"
    assert sentinela not in llm.prompts[0], f"{sitio['sitio']}: o valor da chave nova chegou ao prompt"
    assert "chave_nova_sintetica" not in llm.prompts[0]


async def test_chave_nova_num_montador_e_acusada(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prova de nao-vacuidade (revisao de e9b3c3bd, F1): uma chave nova em
    `ValentinaGraph._care_facts` some do prompt pelo renderizador, que CONTA o descarte, E faz o
    teste de referencia falhar."""
    from maezo.agents.valentina.graph import ValentinaGraph

    original = ValentinaGraph._care_facts

    def _com_chave_nova(state: Any) -> dict[str, Any]:
        return {**original(state), "chave_nova_sintetica": "SENTINELA-NOVA-0001"}

    monkeypatch.setattr(ValentinaGraph, "_care_facts", staticmethod(_com_chave_nova))
    sitio = next(s for s in _SITIOS if s["sitio"] == "valentina._build_dossier/stratify")
    antes = _descartes("valentina")

    chamadas, fatos = await _rodar(sitio, monkeypatch)

    chaves, permitidos = chamadas[0]
    assert "chave_nova_sintetica" in chaves - permitidos  # o renderizador a descarta...
    assert _descartes("valentina") == antes + 1  # ...conta o descarte...
    assert fatos is not None
    assert "chave_nova_sintetica" in set(fatos) - permitidos  # ...ela segue no dossie...
    assert set(fatos) - permitidos != DESCARTE_ESPERADO.get(sitio["sitio"], frozenset())  # ...e e' acusada
