"""DL-0191: o texto de cada prompt so' pede ao modelo campo que a lista fechada do sitio manda.

Ver `tests/support/contrato_do_prompt.py`. Para adotar outro agente, acrescente UMA linha a
`_TEMPLATES`: (sitio, funcao do template, lista do sitio, marcador de inicio, marcador de fim).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from maezo.agents.andre import graph as andre
from maezo.agents.andre import prompts as andre_prompts
from maezo.agents.beatriz import graph as beatriz
from maezo.agents.beatriz import prompts as beatriz_prompts
from maezo.agents.carolina import graph as carolina
from maezo.agents.carolina import prompts as carolina_prompts
from maezo.agents.fernando import graph as fernando
from maezo.agents.fernando import prompts as fernando_prompts
from maezo.agents.gustavo import graph as gustavo
from maezo.agents.gustavo import prompts as gustavo_prompts
from maezo.agents.lucas import graph as lucas
from maezo.agents.lucas import prompts as lucas_prompts
from maezo.agents.marina import graph as marina
from maezo.agents.marina import prompts as marina_prompts
from maezo.agents.rafael import graph as rafael
from maezo.agents.rafael import prompts as rafael_prompts
from maezo.agents.valentina import graph as valentina
from maezo.agents.valentina import prompts as valentina_prompts
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
    # U1c: os outros sete agentes do dossie (o fim e' o comeco das proibicoes, que NOMEIAM o dado).
    (
        "valentina.stratify",
        valentina_prompts.stratification_prompt,
        valentina._PERMITIDOS_PROMPT,
        "Tarefa:",
        "Cada",
    ),
    (
        "valentina.enroll",
        valentina_prompts.care_plan_prompt,
        valentina._PERMITIDOS_PROMPT,
        "Tarefa:",
        "O plano",
    ),
    (
        "gustavo.ans",
        gustavo_prompts.dossier_prompt,
        gustavo._PERMITIDOS_PROMPT_ANS_SUBMIT,
        "Tarefa:",
        "Cite os",
    ),
    ("gustavo.nip", gustavo_prompts.dossier_prompt, gustavo._PERMITIDOS_PROMPT_NIP, "Tarefa:", "Cite os"),
    (
        "fernando.msg",
        fernando_prompts.message_prompt,
        fernando._PERMITIDOS_PROMPT_MENSAGEM,
        "Tarefa:",
        "NUNCA",
    ),
    (
        "fernando.dossie",
        fernando_prompts.dossier_prompt,
        fernando._PERMITIDOS_PROMPT_DOSSIE,
        "Tarefa:",
        "Cite os",
    ),
    ("andre.dossier", andre_prompts.dossier_prompt, andre._PERMITIDOS_PROMPT, "Tarefa:", "Cite os"),
    ("beatriz.dossier", beatriz_prompts.dossier_prompt, beatriz._PERMITIDOS_PROMPT, "Tarefa:", "NAO acuse"),
    ("carolina.dossier", carolina_prompts.dossier_prompt, carolina._PERMITIDOS_PROMPT, "Tarefa:", "Cite os"),
    ("lucas.message", lucas_prompts.message_prompt, lucas._PERMITIDOS_PROMPT, "Tarefa:", "TOM DA CONVERSA"),
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
        ("o numero da NIP e a ordem de pagamento", ["numero_nip_ans", "ordem_pagamento_id"]),
        (
            "o numero do contrato, o numero do caso e os ponteiros",
            ["feature_snapshot_ref", "numero_caso", "numero_contrato"],
        ),
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
