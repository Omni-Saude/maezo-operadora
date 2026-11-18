"""DL-0191: o texto de cada prompt so' pede ao modelo campo que a lista fechada do sitio manda.

Ver `tests/support/contrato_do_prompt.py`. Para adotar outro agente (U1c), acrescente UMA linha a
`_TEMPLATES`: (sitio, funcao do template, lista do sitio, marcador de inicio, marcador de fim).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from maezo.agents.marina import graph as marina
from maezo.agents.marina import prompts as marina_prompts
from maezo.agents.rafael import graph as rafael
from maezo.agents.rafael import prompts as rafael_prompts
from tests.support.contrato_do_prompt import (
    VOCABULARIO_DE_CAMPOS,
    campos_pedidos_fora_da_lista,
)

_Template = tuple[str, Callable[[], str], frozenset[str], str, str]

_TEMPLATES: list[_Template] = [
    ("rafael.dossier", rafael_prompts.dossier_prompt, rafael._PERMITIDOS_PROMPT, "Tarefa:", "Cada fato vem"),
    (
        "marina.contas",
        marina_prompts.dossier_prompt,
        marina._PERMITIDOS_PROMPT_CONTAS,
        "Tarefa:",
        "REGRAS DURAS:",
    ),
    (
        "marina.recurso",
        marina_prompts.recurso_prompt,
        marina._PERMITIDOS_PROMPT_RECURSO,
        "Tarefa:",
        "REGRAS DURAS:",
    ),
    (
        "marina.reembolso",
        marina_prompts.reembolso_prompt,
        marina._PERMITIDOS_PROMPT_REEMBOLSO,
        "Tarefa:",
        "REGRAS DURAS:",
    ),
]


@pytest.mark.parametrize(("sitio", "template", "permitidos", "inicio", "fim"), _TEMPLATES, ids=lambda x: x)
def test_o_template_nao_pede_campo_fora_da_lista(
    sitio: str, template: Callable[[], str], permitidos: frozenset[str], inicio: str, fim: str
) -> None:
    fora = campos_pedidos_fora_da_lista(template(), permitidos=permitidos, inicio=inicio, fim=fim)

    assert not fora, f"{sitio}: o template pede {fora}, que a lista do sitio nao manda ao modelo (DL-0191)"


# --- o proprio contrato ------------------------------------------------------------------------

_SINTETICO = """Voce nunca usa CPF, nome nem CNS.

Tarefa: resuma o caso com {pedido} e o valor.

REGRAS DURAS: nunca cite a guia."""


@pytest.mark.parametrize(
    ("pedido", "esperado"),
    [
        ("lote/guia/conta TISS", ["numero_conta", "numero_guia_tiss", "numero_lote_tiss"]),
        ("o glosa_id e o CID-10", ["cid10", "glosa_id"]),
        ("o protocolo de reembolso e o paciente", ["protocolo_reembolso", "relacao_com_titular"]),
        ("os documentos enviados e o resumo FHIR", ["documentos_refs", "patient_facts"]),
        ("o tipo de lote", []),
    ],
)
def test_o_contrato_acusa_o_campo_pedido(pedido: str, esperado: list[str]) -> None:
    fora = campos_pedidos_fora_da_lista(
        _SINTETICO.format(pedido=pedido), permitidos=frozenset(), inicio="Tarefa:", fim="REGRAS DURAS:"
    )

    assert fora == esperado


def test_o_contrato_nao_le_o_sistema_nem_as_regras_duras() -> None:
    """A proibicao ("nunca CPF", "nunca cite a guia") fica fora do trecho do pedido."""
    fora = campos_pedidos_fora_da_lista(
        _SINTETICO.format(pedido="o tipo de reembolso"),
        permitidos=frozenset(),
        inicio="Tarefa:",
        fim="REGRAS DURAS:",
    )

    assert fora == []


def test_campo_na_lista_do_sitio_pode_ser_pedido() -> None:
    fora = campos_pedidos_fora_da_lista(
        _SINTETICO.format(pedido="a guia"),
        permitidos=frozenset({"numero_guia_tiss"}),
        inicio="Tarefa:",
        fim="REGRAS DURAS:",
    )

    assert fora == []


def test_o_vocabulario_nao_tem_campo_que_um_sitio_manda() -> None:
    """Vocabulario so' de campo do caso que nao vai ao modelo: nenhum sitio adotado o manda."""
    for sitio, _, permitidos, _, _ in _TEMPLATES:
        assert not set(VOCABULARIO_DE_CAMPOS) & permitidos, sitio
