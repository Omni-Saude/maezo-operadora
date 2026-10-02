"""Bateria de 01/10/2026 (E08): crise de ansiedade/panico e' P1, como o roteamento de saude mental (DL-0064).

Decisao INTERINA. O cabecalho da tabela e o `escalation_routing` r2 ja' diziam que saude mental e'
sempre P1; a linha r5 (P2) contradizia os dois. Nao ratificada pelo dono clinico.
"""

from __future__ import annotations

import pytest

from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table


@pytest.mark.parametrize("codigo", ["crise_panico", "crise_ansiedade"])
def test_e08_crise_de_panico_e_p1_como_o_roteamento_de_saude_mental(codigo: str) -> None:
    veredito = evaluate(
        read_live_table(DMN_DIR / "triage_redflag_mental_health.dmn"),
        {"sintoma_codigo": codigo, "intensidade": "desconhecida", "risco_imediato": False},
    )
    assert (veredito.saidas["red_flag"], veredito.saidas["prioridade"], veredito.saidas["conduta"]) == (
        True,
        "P1",
        "ESCALATE_EMERGENCY",
    )
