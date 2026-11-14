"""Guardas de liberacao de PAGTO: teto do aprovado (R-053/D10) e segregacao de funcoes (SoD).

Pacote PAGTO PR-A (analyses/PAGTO-balanced-classification.md §9, aceito pelo dono em 2026-10-31,
#1084 Rodada 2 "Pagamentos": "teto do aprovado como saida da DMN lida pelo guard").

1. TETO DO APROVADO. `pagto_alcada` emite `valor_maximo_aprovavel_cents` (a regra "teto = valor da
   ordem" vive na DMN, decisao R-053 (a)); `BRT_AlcadaRouting` a achata no escopo; o worker gated
   `release_high_value_payment` RECUSA `valor_aprovado_cents > valor_maximo_aprovavel_cents` e
   RECUSA quando o teto esta ausente/mal-formado (fail-closed). Nenhuma comparacao com o valor da
   ordem e feita em Python: o worker so le o fato que a DMN emitiu.
2. SoD. (a) o analista de admissibilidade nao pode ser o decisor do lastro na origem;
   (b) o aprovador de alcada nao pode ser o decisor do lastro nem o analista de admissibilidade.
   Violacao -> `ERR_PAYMENT_SOD_NOT_HUMAN` (sufixo `_NOT_HUMAN`: entra na trilha de recusa auditada
   T-E por padrao, `harness.is_guard_refusal_code` — uma recusa de SoD nunca e silenciosa).

Estilo guard-test: cada vetor de bypass e um caso negativo proprio; o caminho feliz (sem
identidades coincidentes, aprovado <= teto) e provado inalterado.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.harness import is_guard_refusal_code
from maezo.tools.workers.pagto import (
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    ERR_PAYMENT_SOD_NOT_HUMAN,
    PagtoError,
    execute_pagto,
    release_high_value_payment,
)

_ABSENT = object()


def _liberacao(**overrides: Any) -> dict[str, Any]:
    """Escopo de processo no `ST_ReleaseHighValue` do caminho feliz (tudo satisfeito)."""
    base: dict[str, Any] = {
        "decisao_pagamento": "APROVAR",
        "aprovador_id": "fin-aprovador-001",
        "aprovador_tier": 2,
        "justificativa_aprovacao": "Fundamentacao sintetica de liberacao",
        "valor_aprovado_cents": 120_000_000,
        "valor_maximo_aprovavel_cents": 120_000_000,
        "faixa_valor": "ALCADA_L2",
        "tier_minimo": 2,
        "lastro_decisor_id": "analista-contas-007",
        "analista_admissibilidade_id": "coord-fin-003",
    }
    for key, value in overrides.items():
        if value is _ABSENT:
            base.pop(key, None)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------------------------
# 1. Teto do aprovado — saida da DMN lida pelo guard
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("aprovado", [1, 60_000_000, 119_999_999, 120_000_000])
def test_aprovado_ate_o_teto_da_dmn_libera(aprovado: int) -> None:
    result = release_high_value_payment(_liberacao(valor_aprovado_cents=aprovado))
    assert result == {"pagamento_liberado": True, "tipo_liberacao": "humano_alcada"}


@pytest.mark.parametrize("aprovado", [120_000_001, 240_000_000, 2**63 - 1])
def test_aprovado_acima_do_teto_da_dmn_recusa(aprovado: int) -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_liberacao(valor_aprovado_cents=aprovado))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "valor_maximo_aprovavel_cents" in excinfo.value.message


def test_recusa_por_teto_nao_vaza_os_valores() -> None:
    """Texto de incidente carrega so o token, nunca os centavos (mesma regra do F-041)."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            _liberacao(valor_aprovado_cents=987_654_321, valor_maximo_aprovavel_cents=123_456_789)
        )
    assert "987654321" not in excinfo.value.message
    assert "123456789" not in excinfo.value.message


@pytest.mark.parametrize(
    "teto",
    [_ABSENT, None, 0, -1, "120000000", 120_000_000.0, True, False, [120_000_000], 2**63],
    ids=[
        "ausente",
        "none",
        "zero",
        "negativo",
        "string-numerica",
        "float-integral",
        "bool-true",
        "bool-false",
        "lista",
        "acima-int64",
    ],
)
def test_teto_ausente_ou_mal_formado_recusa_fail_closed(teto: object) -> None:
    """Sem o fato da DMN no escopo nao ha liberacao — nunca um default permissivo."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_liberacao(valor_maximo_aprovavel_cents=teto))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "valor_maximo_aprovavel_cents" in excinfo.value.message


def test_worker_nao_recompara_com_o_valor_da_ordem_em_python() -> None:
    """A regra vive na DMN: o worker ignora `valor_pagamento_cents` e le SO o teto emitido."""
    liberado = release_high_value_payment(
        _liberacao(valor_pagamento_cents=1, valor_aprovado_cents=120_000_000)
    )
    assert liberado["pagamento_liberado"] is True
    with pytest.raises(PagtoError):
        release_high_value_payment(_liberacao(valor_pagamento_cents=10**12, valor_aprovado_cents=120_000_001))


# ---------------------------------------------------------------------------------------------
# 2. Segregacao de funcoes — (b) aprovador de alcada
# ---------------------------------------------------------------------------------------------


def test_codigo_sod_entra_na_trilha_de_recusa_auditada() -> None:
    assert is_guard_refusal_code(ERR_PAYMENT_SOD_NOT_HUMAN)


@pytest.mark.parametrize(
    "decisor",
    ["fin-aprovador-001", "  fin-aprovador-001  ", "FIN-APROVADOR-001", "fin-aprovador-001\n"],
    ids=["identico", "espacos", "caixa", "quebra-de-linha"],
)
def test_aprovador_igual_ao_decisor_do_lastro_recusa(decisor: str) -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_liberacao(lastro_decisor_id=decisor))
    assert excinfo.value.code == ERR_PAYMENT_SOD_NOT_HUMAN


@pytest.mark.parametrize(
    "analista",
    ["fin-aprovador-001", " FIN-aprovador-001 "],
    ids=["identico", "espacos-e-caixa"],
)
def test_aprovador_igual_ao_analista_de_admissibilidade_recusa(analista: str) -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_liberacao(analista_admissibilidade_id=analista))
    assert excinfo.value.code == ERR_PAYMENT_SOD_NOT_HUMAN


def test_analista_igual_ao_decisor_do_lastro_recusa_na_alcada() -> None:
    """(a) tambem barra a liberacao de alto valor: o PROSSEGUIR foi dado pelo proprio decisor."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            _liberacao(lastro_decisor_id="coord-fin-003", analista_admissibilidade_id="coord-fin-003")
        )
    assert excinfo.value.code == ERR_PAYMENT_SOD_NOT_HUMAN


def test_recusa_sod_nao_vaza_identidades() -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_liberacao(lastro_decisor_id="fin-aprovador-001"))
    assert "fin-aprovador-001" not in excinfo.value.message


@pytest.mark.parametrize(
    "overrides",
    [
        {"lastro_decisor_id": _ABSENT, "analista_admissibilidade_id": _ABSENT},
        {"lastro_decisor_id": "", "analista_admissibilidade_id": ""},
        {"lastro_decisor_id": "   ", "analista_admissibilidade_id": None},
        {"lastro_decisor_id": 7, "analista_admissibilidade_id": ["x"]},
    ],
    ids=["ausentes", "vazios", "brancos-none", "nao-string"],
)
def test_identidades_de_origem_ausentes_nao_mudam_o_caminho_feliz(overrides: dict[str, Any]) -> None:
    """Perna automatica de CONTAS (`lastro_decisor_id` vazio) e ordem que nao passou pela
    admissibilidade humana: nao ha a quem comparar — o guard nao inventa uma violacao."""
    assert release_high_value_payment(_liberacao(**overrides))["pagamento_liberado"] is True


def test_sod_e_teto_reportados_juntos_prevalece_o_codigo_sod() -> None:
    """Violacao de SoD nunca e mascarada por outra recusa: o codigo e o de SoD."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            _liberacao(lastro_decisor_id="fin-aprovador-001", valor_aprovado_cents=999_999_999)
        )
    assert excinfo.value.code == ERR_PAYMENT_SOD_NOT_HUMAN
    assert "valor_maximo_aprovavel_cents" in excinfo.value.message


# ---------------------------------------------------------------------------------------------
# 3. Segregacao de funcoes — (a) na faixa clerical (ST_ReleaseLowValue apos PROSSEGUIR)
# ---------------------------------------------------------------------------------------------


def _clerical(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "faixa_valor": "DENTRO_TETO_L2",
        "ordem_pagamento_id": "OP-SINT-1",
        "valor_pagamento_cents": 85_000,
    }
    base.update(overrides)
    return base


def test_clerical_sem_analise_humana_inalterado() -> None:
    assert execute_pagto(_clerical()) == {"pagamento_executado": True, "tipo_liberacao": "auto_L2"}


def test_clerical_com_analista_distinto_do_decisor_inalterado() -> None:
    result = execute_pagto(
        _clerical(lastro_decisor_id="analista-contas-007", analista_admissibilidade_id="coord-fin-003")
    )
    assert result["pagamento_executado"] is True


@pytest.mark.parametrize("analista", ["analista-contas-007", " Analista-Contas-007 "])
def test_clerical_analista_igual_ao_decisor_do_lastro_recusa(analista: str) -> None:
    with pytest.raises(PagtoError) as excinfo:
        execute_pagto(
            _clerical(lastro_decisor_id="analista-contas-007", analista_admissibilidade_id=analista)
        )
    assert excinfo.value.code == ERR_PAYMENT_SOD_NOT_HUMAN


def test_clerical_faixa_incompativel_continua_neutra_mesmo_com_sod() -> None:
    """Fora de DENTRO_TETO_L2 nada e executado — a recusa neutra pre-existente nao muda."""
    result = execute_pagto(
        _clerical(
            faixa_valor="ALCADA_L1",
            lastro_decisor_id="analista-contas-007",
            analista_admissibilidade_id="analista-contas-007",
        )
    )
    assert result == {"pagamento_executado": False}
