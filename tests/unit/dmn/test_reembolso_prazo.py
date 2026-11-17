"""E-SF-08 / TB-E-16-a + EP-REG-G4a — prazo de reembolso: so' no ramo urgencia/emergencia, relogio a partir do
fato `documentacao_adequada_em` (registrado pelo analista), enquadramento nunca autodeclarado, nenhum timer
DRAFT nem ETA no assistente de reembolso.

`reembolso_prazo.dmn` continua so' com o catch-all `NAO_AVALIADO` (ele mede dias desde o ATENDIMENTO — prazo
para pedir, nao para pagar). O dispositivo de pagamento (Lei 9.656 art. 12 VI) vive no catalogo, DRAFT.
"""

from __future__ import annotations

import dataclasses
import inspect

from tests.support.dmn_first_hit import DMN_DIR, DecisionTable, Rule, read_live_table
from tests.unit.dmn._catalogo_prazos import (
    ANCORA_REEMBOLSO,
    ENQUADRAMENTO_CONFIAVEL,
    SEM_ETA_NAO_RATIFICADO,
    LinhaCatalogo,
    auditar_catalogo,
    auditar_reembolso_prazo,
    auditar_textos_ao_comprador,
    linha_para,
    linhas,
    motivo_eta,
    textos_do_assistente_de_reembolso,
)

_REEMBOLSO_PRAZO = DMN_DIR / "reembolso_prazo.dmn"


def _lei() -> LinhaCatalogo:
    return linha_para("Lei9656-art12-VI")


def _tabela_injetada(entradas: tuple[str, ...], regra: Rule) -> DecisionTable:
    viva = read_live_table(_REEMBOLSO_PRAZO)
    catchall = Rule(rule_id="r_catchall", inputs=("-",) * len(entradas), outputs=("NAO_AVALIADO",))
    return dataclasses.replace(viva, input_names=entradas, rules=(regra, catchall))


def test_refund_deadline_only_for_urgency() -> None:
    viva = read_live_table(_REEMBOLSO_PRAZO)
    assert [r.outputs for r in viva.rules] == [("NAO_AVALIADO",)]
    assert auditar_reembolso_prazo(viva) == []
    assert {x.escopo for x in linhas() if x.escopo.startswith("reembolso_")} == {
        "reembolso_urgencia_emergencia"
    }
    injetada = _tabela_injetada(
        ("tipo_reembolso", "dias_desde_documentacao_adequada"),
        Rule(rule_id="r_livre", inputs=('"livre_escolha"', "<= 30"), outputs=("DENTRO",)),
    )
    assert auditar_reembolso_prazo(injetada) == [("r_livre", "REFUND_DEADLINE_WITHOUT_ANCHOR")]
    fora = dataclasses.replace(_lei(), escopo="reembolso_livre_escolha")
    assert auditar_catalogo([fora]) == [("r_lei9656_art12_vi", "REFUND_DEADLINE_WITHOUT_ANCHOR")]


def test_refund_clock_starts_at_adequate_docs_fact() -> None:
    assert _lei().evento_ancora_ref == ANCORA_REEMBOLSO
    da_submissao = dataclasses.replace(_lei(), evento_ancora_ref="data_solicitacao")
    assert auditar_catalogo([da_submissao]) == [("r_lei9656_art12_vi", "REFUND_CLOCK_START_INVALID")]
    injetada = _tabela_injetada(
        ("tipo_reembolso", "data_submissao"),
        Rule(rule_id="r_urg", inputs=('"urgencia_emergencia"', "<= 30"), outputs=("DENTRO",)),
    )
    assert auditar_reembolso_prazo(injetada) == [("r_urg", "REFUND_CLOCK_START_INVALID")]


def test_draft_timers_never_buyer_visible() -> None:
    assert "Lei 9.656/1998, art. 12, VI" in _lei().dispositivo_citacao
    assert auditar_textos_ao_comprador(textos_do_assistente_de_reembolso()) == []
    assert auditar_textos_ao_comprador({"aviso": "Você recebe em até P30D."}) == [
        ("aviso", "DRAFT_DEADLINE_DISCLOSED")
    ]
    assert auditar_textos_ao_comprador({"aviso": "O pagamento sai em até 30 dias."}) == [
        ("aviso", "DRAFT_DEADLINE_DISCLOSED")
    ]


def test_refund_wizard_no_eta_outside_emergency() -> None:
    from maezo.domain.reembolso.pedido import TIPOS_REEMBOLSO

    for tipo in TIPOS_REEMBOLSO:
        linha = linha_para(f"reembolso-{tipo}")
        assert linha.regra == "r_catchall"
        assert motivo_eta(linha, ancora_observada=True) == SEM_ETA_NAO_RATIFICADO
    # Nem o ramo de urgencia tem ETA nesta onda (DRAFT; RL-REG-44).
    assert motivo_eta(_lei(), ancora_observada=True) == SEM_ETA_NAO_RATIFICADO


def test_refund_emergency_flag_not_self_declared() -> None:
    from maezo.domain.reembolso import pedido

    assert _lei().enquadramento_fonte == ENQUADRAMENTO_CONFIAVEL
    fonte = inspect.getsource(pedido)
    assert "funnel_deadline_catalog" not in fonte and "Lei9656-art12-VI" not in fonte
    autodeclarada = dataclasses.replace(_lei(), enquadramento_fonte="autodeclaracao_beneficiario")
    assert auditar_catalogo([autodeclarada]) == [("r_lei9656_art12_vi", "emergency_flag_untrusted_source")]
