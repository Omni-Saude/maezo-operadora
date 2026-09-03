"""Unit tests for maezo.tools.workers.adequacao (SP-OP-ADEQUACAO-001).

TDD London School: tests verify gap measurement and the human-gated fallback commitment.
"""

from typing import Any

import pytest
import structlog

from maezo.a2a import DelegationResult, RejectionReason
from maezo.tools.workers.adequacao import (
    ERR_FALLBACK_COMMITMENT_NOT_HUMAN,
    AdequacaoError,
    execute_remediation,
    make_prepare_remediation_dossier_handler,
    make_update_monitoring_plan_handler,
    measure_gap,
    notify_coordenacao,
    notify_sla_risk,
    register_adequacao_workers,
    register_fallback_commitment,
    route_remediation,
    update_monitoring_plan,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerHarness


def _adequacao_fake(*, gap_adequacao: str, roteamento_remediacao: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("adequacao_gap", [{"gap_adequacao": gap_adequacao, "motivo": motivo}])
    fake.register(
        "adequacao_remediation_routing",
        [{"roteamento_remediacao": roteamento_remediacao, "motivo": motivo}],
    )
    return fake


# ---------------------------------------------------------------
# measure_gap
# ---------------------------------------------------------------


def test_measure_gap_returns_facts() -> None:
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "tipo_carater": "eletivo",
            "prestadores_disponiveis": 3,
        }
    )
    assert result["tempo_acesso_apurado_min"] == 45
    assert result["distancia_apurada_km"] == 15.5
    assert result["prestadores_disponiveis"] == 3
    assert result["cobertura_geo_suficiente"] is True
    assert result["dados_geo_completos"] is True


def test_measure_gap_insufficient_data() -> None:
    result = measure_gap(
        {
            "regiao_saude": "",
            "especialidade": "",
        }
    )
    assert result["dados_geo_completos"] is False


# ---------------------------------------------------------------
# measure_gap — FACT PRESERVATION (mirrors credenciamento.validate_cred's fix, the identical
# defect class: an already-resolved fact on `variables` must be ECHOED, never clobbered by the
# placeholder/re-derivation before BRT_AdequacaoGap evaluates it).
# ---------------------------------------------------------------


def test_measure_gap_preserves_resolved_tempo_acesso() -> None:
    """An explicit, correctly-typed (int, non-bool) `tempo_acesso_apurado_min` is echoed
    through unchanged -- NOT clobbered by the hardcoded 45min placeholder."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "tempo_acesso_apurado_min": 12}
    )
    assert result["tempo_acesso_apurado_min"] == 12


def test_measure_gap_tempo_acesso_absent_falls_back_to_placeholder() -> None:
    result = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["tempo_acesso_apurado_min"] == 45


@pytest.mark.parametrize("wrong_type", [True, False, "12", 12.0, None, [12], {"v": 12}])
def test_measure_gap_tempo_acesso_wrong_type_falls_back_to_placeholder(wrong_type: object) -> None:
    """A wrong-typed `tempo_acesso_apurado_min` (incl. `bool` -- a subclass of `int` in Python,
    but NEVER a valid numeric measurement here) is NOT treated as a resolved fact -- falls back
    to the placeholder, exactly like absent."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "tempo_acesso_apurado_min": wrong_type}
    )
    assert result["tempo_acesso_apurado_min"] == 45


def test_measure_gap_preserves_resolved_distancia_apurada() -> None:
    """An explicit, correctly-typed (float) `distancia_apurada_km` is echoed through unchanged
    -- NOT clobbered by the hardcoded 15.5km placeholder."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "distancia_apurada_km": 3.2}
    )
    assert result["distancia_apurada_km"] == 3.2


def test_measure_gap_distancia_apurada_absent_falls_back_to_placeholder() -> None:
    result = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["distancia_apurada_km"] == 15.5


@pytest.mark.parametrize("wrong_type", [True, False, "3.2", 3, None, [3.2], {"v": 3.2}])
def test_measure_gap_distancia_apurada_wrong_type_falls_back_to_placeholder(wrong_type: object) -> None:
    """A wrong-typed `distancia_apurada_km` (incl. `bool`, and incl. a plain `int` -- the
    contract types this field `double`, never `number`/`integer`) falls back to the placeholder,
    exactly like absent."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "distancia_apurada_km": wrong_type}
    )
    assert result["distancia_apurada_km"] == 15.5


def test_measure_gap_preserves_resolved_cobertura_geo_suficiente_true() -> None:
    """An already-resolved `cobertura_geo_suficiente=True` is respected even when the
    prestadores-derived heuristic would say otherwise (prestadores<2 -> heuristic False)."""
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": True,
        }
    )
    assert result["cobertura_geo_suficiente"] is True


def test_measure_gap_preserves_resolved_cobertura_geo_suficiente_false() -> None:
    """An already-resolved `cobertura_geo_suficiente=False` is respected even when the
    prestadores-derived heuristic would say otherwise (prestadores>=2 -> heuristic True) --
    the FIX must be strictly MORE conservative than the pre-fix always-recompute behavior,
    never less."""
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": False,
        }
    )
    assert result["cobertura_geo_suficiente"] is False


@pytest.mark.parametrize("wrong_type", ["true", 1, 0, None, [], {}])
def test_measure_gap_cobertura_geo_suficiente_wrong_type_falls_back_to_derivation(wrong_type: object) -> None:
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": wrong_type,
        }
    )
    assert result["cobertura_geo_suficiente"] is True  # prestadores=5 >= 2 -> derived True


def test_measure_gap_preserves_resolved_dados_geo_completos_true() -> None:
    """An already-resolved `dados_geo_completos=True` is respected even when regiao/especialidade
    are blank (the shape-derivation heuristic would say False)."""
    result = measure_gap({"regiao_saude": "", "especialidade": "", "dados_geo_completos": True})
    assert result["dados_geo_completos"] is True


def test_measure_gap_preserves_resolved_dados_geo_completos_false() -> None:
    """An already-resolved `dados_geo_completos=False` is respected even when regiao/especialidade
    are both present (the shape-derivation heuristic would say True)."""
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "dados_geo_completos": False}
    )
    assert result["dados_geo_completos"] is False


@pytest.mark.parametrize("wrong_type", ["true", 1, 0, None, [], {}])
def test_measure_gap_dados_geo_completos_wrong_type_falls_back_to_derivation(wrong_type: object) -> None:
    result = measure_gap(
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "dados_geo_completos": wrong_type}
    )
    assert result["dados_geo_completos"] is True  # regiao and especialidade both present -> True


def test_measure_gap_all_four_facts_preserved_simultaneously_prestadores_unaffected() -> None:
    """All four previously-clobbered facts respected at once; `prestadores_disponiveis`
    (already preserved pre-fix) and the unrelated identity fields stay unchanged -- pins that
    the fix touches ONLY the four clobbered keys."""
    result = measure_gap(
        {
            "regiao_saude": "R-002",
            "especialidade": "ortopedia",
            "tempo_acesso_apurado_min": 8,
            "distancia_apurada_km": 1.1,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": False,
            "prestadores_disponiveis": 7,
        }
    )
    assert result == {
        "tempo_acesso_apurado_min": 8,
        "distancia_apurada_km": 1.1,
        "prestadores_disponiveis": 7,
        "cobertura_geo_suficiente": False,
        "dados_geo_completos": False,
    }


# ---------------------------------------------------------------
# route_remediation
# ---------------------------------------------------------------


def test_adequacao_gap_conforme_now_leve() -> None:
    """golden-parity finding (T1.5, live-verified): the deployed adequacao_gap table's
    GAP_LEVE row (tipo_carater="eletivo", tempo<=60, distancia<=50.0, prestadores>0,
    cobertura=true) precedes CONFORME's in FIRST-hit-policy order and is LESS restrictive than
    the old Python's CONFORME gate (tempo<=30, distancia<=20.0) — this exact input (which the
    old Python called CONFORME) is actually GAP_LEVE on the real engine. DMN wins (documented,
    not patched); zero functional impact — CONFORME and GAP_LEVE route to the SAME MONITORAR
    remediation (live-verified)."""
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 15,
            "distancia_apurada_km": 5.0,
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_conforme_reachable_beyond_leve_gate() -> None:
    """CONFORME is still reachable — live-verified: when tempo exceeds GAP_LEVE's own gate
    (tempo<=60) but prestadores>0 and cobertura=true, the DMN's gateless CONFORME row (wildcard
    on tempo/distancia) matches. Also documents a DMN-content quirk (not patched, per
    constraint 5): CONFORME's row does not itself gate on tempo/distancia, so this specific
    case (tempo=70min) reads as MORE severe than the tempo=45min GAP_LEVE case yet is labeled
    CONFORME — flagged for spec-side review, not fixed here.

    FAIL-SAFE (decisao do dono, 2026-08-06): o veredito da DMN continua CONFORME (nao reescrevemos
    a tabela), MAS o worker RECUSA agir sobre ele quando o acesso medido excede o teto que a
    propria tabela declara em `r_eletivo_leve` (60min/50km) — o roteamento vira ANALISE_HUMANA em
    vez de MONITORAR. Sem isto, este caso (70min) terminava em End_AdequacaoConforme SEM plano de
    monitoramento e SEM alerta a gestao-rede, pior que o comportamento anterior a preservacao de
    fatos."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 70,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    # veredito da DMN preservado e auditavel...
    assert result["gap_adequacao"] == "CONFORME"
    # ...mas NAO se age sobre ele: 70min excede o teto eletivo da propria tabela (60min).
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"
    assert result["motivo"] == "CONFORME_RECUSADO_ACESSO_ACIMA_DO_TETO_LEVE"


def test_conforme_dentro_do_teto_leve_nao_e_recusado() -> None:
    """Contra-prova de nao-vacuidade: CONFORME DENTRO do teto (45min/10km) segue MONITORAR.

    Sem este teste o fail-safe poderia recusar tudo e ainda parecer correto."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_conforme_recusado_tambem_por_distancia() -> None:
    """A recusa dispara pelo teto de DISTANCIA tambem, nao so pelo de tempo."""
    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 30,
            "distancia_apurada_km": 80.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_gap_leve_acima_do_teto_nao_e_afetado_pelo_fail_safe() -> None:
    """O fail-safe so alcanca CONFORME — um GAP_LEVE segue seu roteamento normal."""
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 200,
            "distancia_apurada_km": 90.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_leve() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_LEVE", roteamento_remediacao="MONITORAR")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_moderado() -> None:
    """golden-parity note: the DMN computes GAP_MODERADO differently — keyed off
    cobertura_geo_suficiente=false (wildcard on tempo/distancia/prestadores), not the old
    Python's prestadores>=1 and tempo<=90. Same live-verified inputs, same result."""
    fake = _adequacao_fake(gap_adequacao="GAP_MODERADO", roteamento_remediacao="ENCAMINHAR_CREDENCIAMENTO")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 75,
            "distancia_apurada_km": 30.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_MODERADO"
    assert result["roteamento_remediacao"] == "ENCAMINHAR_CREDENCIAMENTO"


def test_adequacao_gap_critico() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_CRITICO", roteamento_remediacao="ANALISE_HUMANA")
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 120,
            "distancia_apurada_km": 50.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_adequacao_gap_dados_incompletos() -> None:
    fake = _adequacao_fake(gap_adequacao="GAP_CRITICO", roteamento_remediacao="ANALISE_HUMANA")
    result = route_remediation(
        {
            "dados_geo_completos": False,
            "tempo_acesso_apurado_min": 0,
            "distancia_apurada_km": 0.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_route_remediation_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        route_remediation({"tipo_carater": "eletivo"}, dmn=None)


# ---------------------------------------------------------------
# route_remediation — M-1: the two BLIND SPOTS of the owner-ratified fail-safe
#
# The ratified branch (adequacao.py:361-371) only ever fires on ELECTIVE-GRADE ceilings
# (60min/50km, `_ELETIVO_LEVE_*`), for EVERY `tipo_carater`. Two families slip past it while the
# DMN still says CONFORME -> `GW_Roteamento` sends `MONITORAR && CONFORME` to
# `ST_PublishConforme`/`End_AdequacaoConforme`: NO monitoring plan, NO alert.
#
#   (a) `tipo_carater` blank or outside the closed vocabulary the table declares
#       ({eletivo, urgencia_emergencia} — adequacao_gap.dmn:52,62,92,102; the other four rows
#       wildcard the column at :72,:82,:112,:122). `{tipo_carater: "", 45min, 40km}` read CONFORME
#       and published silently, while the SAME measurements under `urgencia_emergencia` are
#       GAP_CRITICO.
#   (b) measurements indistinguishable from the pair `measure_gap` FABRICATES when nothing typed
#       is seeded (45min + 15.5km) — a pair that sits inside the elective ceiling by construction,
#       so the ratified branch can never reach it.
#
# Both preserve the DMN verdict (auditable) and refuse only to ACT — same shape as the ratified
# branch. The TABLE is untouched (ADR-0028): its correction is the regulatory owner's act, already
# described as ACHADO-1 in spec/processes/dmn/adequacao-gap-shadow-candidate.yaml.
# ---------------------------------------------------------------

_MOTIVO_TETO = "CONFORME_RECUSADO_ACESSO_ACIMA_DO_TETO_LEVE"
_MOTIVO_CARATER = "CONFORME_RECUSADO_CARATER_FORA_DO_VOCABULARIO"
_MOTIVO_PLACEHOLDER = "CONFORME_RECUSADO_MEDIDAS_INDISTINGUIVEIS_DE_PLACEHOLDER"

#: Everything the two blind-spot branches do NOT key off, held constant so each row varies only
#: `tipo_carater` and the two measurements. `prestadores>0` + `cobertura=true` is exactly the
#: subspace where the live table's gateless `r_conforme` (adequacao_gap.dmn:110-119) matches.
_M1_BASE: dict[str, Any] = {
    "prestadores_disponiveis": 2,
    "cobertura_geo_suficiente": True,
    "dados_geo_completos": True,
}

#: (label, tipo_carater overlay, tempo, distancia, expected roteamento, expected motivo).
#: `_ABSENT` means the key is not present on `variables` at all — the real "process started without
#: tipo_carater" shape, which `variables.get("tipo_carater", "")` turns into `""`.
_ABSENT = object()

_M1_MATRIX: tuple[tuple[str, Any, int, float, str, str], ...] = (
    # --- (a) carater fora do vocabulario: TODOS recusados, com o token proprio ------------------
    (
        "carater AUSENTE + medidas dentro do teto eletivo (o caso M-1 do enunciado)",
        _ABSENT,
        45,
        40.0,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
    ("carater string vazia explicita", "", 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    ("carater whitespace-only", "   ", 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    (
        "carater com padding de espaco ' eletivo ' — strip() bateria 'eletivo'; match EXATO nao (F-1)",
        " eletivo ",
        45,
        40.0,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
    (
        "carater tab-prefixado '\\turgencia_emergencia' — strip() bateria; match EXATO nao (F-1)",
        "\turgencia_emergencia",
        45,
        40.0,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
    (
        "carater token desconhecido 'ambulatorial'",
        "ambulatorial",
        45,
        40.0,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
    ("carater variante de caixa 'Eletivo'", "Eletivo", 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    (
        "carater substring 'eletivo_ambulatorial'",
        "eletivo_ambulatorial",
        45,
        40.0,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
    ("carater nao-string None (engine sem tipo)", None, 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    ("carater nao-string int", 0, 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    ("carater nao-string list (nao-hashavel)", ["eletivo"], 45, 40.0, "ANALISE_HUMANA", _MOTIVO_CARATER),
    # --- CONTRA-PROVA: os dois literais DECLARADOS passam intactos -------------------------------
    (
        "eletivo dentro do teto — medida genuina, CONFORME preservado",
        "eletivo",
        45,
        40.0,
        "MONITORAR",
        "m-dmn",
    ),
    ("eletivo no limite exato do teto (60/50.0)", "eletivo", 60, 50.0, "MONITORAR", "m-dmn"),
    ("urgencia_emergencia — rota byte-identica", "urgencia_emergencia", 20, 10.0, "MONITORAR", "m-dmn"),
    ("urgencia_emergencia no limite do teto eletivo", "urgencia_emergencia", 60, 50.0, "MONITORAR", "m-dmn"),
    # --- (b) medidas indistinguiveis do par fabricado --------------------------------------------
    (
        "PAR placeholder exato (45 + 15.5) sob carater declarado",
        "eletivo",
        45,
        15.5,
        "ANALISE_HUMANA",
        _MOTIVO_PLACEHOLDER,
    ),
    (
        "PAR placeholder exato sob urgencia_emergencia",
        "urgencia_emergencia",
        45,
        15.5,
        "ANALISE_HUMANA",
        _MOTIVO_PLACEHOLDER,
    ),
    # --- SUB-INCLUSAO DECLARADA do ramo (b): meio-placeholder NAO e recusado ---------------------
    ("meio-placeholder: so o tempo (45 + 10.0)", "eletivo", 45, 10.0, "MONITORAR", "m-dmn"),
    ("meio-placeholder: so a distancia (20 + 15.5)", "eletivo", 20, 15.5, "MONITORAR", "m-dmn"),
    # --- PRECEDENCIA: o ramo RATIFICADO continua vencendo os dois novos --------------------------
    (
        "teto E carater desconhecido -> vence o token RATIFICADO",
        "",
        300,
        400.0,
        "ANALISE_HUMANA",
        _MOTIVO_TETO,
    ),
    (
        "teto E carater eletivo -> token RATIFICADO (inalterado)",
        "eletivo",
        70,
        10.0,
        "ANALISE_HUMANA",
        _MOTIVO_TETO,
    ),
    (
        "carater desconhecido E par placeholder -> vence o token de CARATER",
        "",
        45,
        15.5,
        "ANALISE_HUMANA",
        _MOTIVO_CARATER,
    ),
)


@pytest.mark.parametrize(
    ("label", "carater", "tempo", "distancia", "esperado_roteamento", "esperado_motivo"), _M1_MATRIX
)
def test_m1_conforme_contraditado_por_carater_ou_medidas_fabricadas(
    label: str,
    carater: Any,
    tempo: int,
    distancia: float,
    esperado_roteamento: str,
    esperado_motivo: str,
) -> None:
    """TABLE-DRIVEN. The DMN says CONFORME/MONITORAR in every row; only the routing decision and
    the `motivo` token differ. The verdict itself is asserted preserved in EVERY row — that is the
    whole shape of this fail-safe family: never rewrite the table's answer, only refuse to act."""
    variables: dict[str, Any] = dict(_M1_BASE)
    variables["tempo_acesso_apurado_min"] = tempo
    variables["distancia_apurada_km"] = distancia
    if carater is not _ABSENT:
        variables["tipo_carater"] = carater

    fake = _adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR", motivo="m-dmn")
    result = route_remediation(variables, dmn=fake)

    # O veredito da DMN e PRESERVADO e auditavel em todas as linhas — inclusive nas recusadas.
    assert result["gap_adequacao"] == "CONFORME", label
    assert result["roteamento_remediacao"] == esperado_roteamento, label
    assert result["motivo"] == esperado_motivo, label


def test_m1_matriz_nao_e_vacua() -> None:
    """NON-VACUITY: the matrix must exercise BOTH outcomes and all three refusal tokens, otherwise
    a fail-safe that refused everything (or nothing) would still read as passing."""
    roteamentos = {row[4] for row in _M1_MATRIX}
    motivos = {row[5] for row in _M1_MATRIX}
    assert roteamentos == {"ANALISE_HUMANA", "MONITORAR"}
    assert {_MOTIVO_TETO, _MOTIVO_CARATER, _MOTIVO_PLACEHOLDER, "m-dmn"} == motivos
    assert len({_MOTIVO_TETO, _MOTIVO_CARATER, _MOTIVO_PLACEHOLDER}) == 3, "tokens must be distinct"


@pytest.mark.parametrize(
    ("gap", "route"),
    [
        ("GAP_LEVE", "MONITORAR"),
        ("GAP_MODERADO", "ENCAMINHAR_CREDENCIAMENTO"),
        ("GAP_CRITICO", "ANALISE_HUMANA"),
    ],
)
@pytest.mark.parametrize(
    ("carater", "tempo", "distancia"),
    [
        ("", 45, 40.0),  # blind spot (a) inputs
        ("ambulatorial", 45, 40.0),  # blind spot (a) inputs
        ("eletivo", 45, 15.5),  # blind spot (b) inputs
        ("", 45, 15.5),  # both blind spots at once
    ],
)
def test_m1_nao_toca_nenhuma_rota_diferente_de_conforme(
    gap: str, route: str, carater: str, tempo: int, distancia: float
) -> None:
    """The two new branches are gated on `gap_adequacao == "CONFORME"`. Fed the EXACT inputs that
    trigger them, every non-CONFORME verdict keeps the DMN's own routing and its own `motivo`."""
    fake = _adequacao_fake(gap_adequacao=gap, roteamento_remediacao=route, motivo="m-dmn")
    result = route_remediation(
        {
            **_M1_BASE,
            "tipo_carater": carater,
            "tempo_acesso_apurado_min": tempo,
            "distancia_apurada_km": distancia,
        },
        dmn=fake,
    )
    assert result["gap_adequacao"] == gap
    assert result["roteamento_remediacao"] == route
    assert result["motivo"] == "m-dmn"


def test_m1_carater_desconhecido_emite_log_com_vocabulario_declarado() -> None:
    """The refusal is AUDITABLE: the operator log names the offending token and the closed
    vocabulary it was checked against — the evidence that replaces the engine variable this
    deliberately does not mint. RAW token via `repr()` (F-1, GK REVISE: not `_norm_str`), so the
    log is never silently normalized into something that reads as in-vocabulary."""
    with structlog.testing.capture_logs() as logs:
        route_remediation(
            {
                **_M1_BASE,
                "tipo_carater": "ambulatorial",
                "tempo_acesso_apurado_min": 45,
                "distancia_apurada_km": 40.0,
            },
            dmn=_adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR"),
        )
    entry = next(e for e in logs if e["event"] == "adequacao_conforme_recusado_por_carater_desconhecido")
    assert entry["tipo_carater"] == repr("ambulatorial")
    assert entry["vocabulario_declarado"] == ["eletivo", "urgencia_emergencia"]
    assert entry["gap_adequacao_dmn"] == "CONFORME"
    assert entry["roteamento_dmn"] == "MONITORAR"
    assert entry["motivo"] == _MOTIVO_CARATER


def test_m1_carater_com_padding_loga_o_padding_visivel() -> None:
    """F-1 (GK REVISE): a padded offender must print VISIBLY padded in the log — `repr()`, never
    `_norm_str`'s strip() — so a reviewer can see the exact token the exact-match guard refused,
    not a normalized look-alike of a valid vocabulary word."""
    with structlog.testing.capture_logs() as logs:
        route_remediation(
            {
                **_M1_BASE,
                "tipo_carater": " eletivo ",
                "tempo_acesso_apurado_min": 45,
                "distancia_apurada_km": 40.0,
            },
            dmn=_adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR"),
        )
    entry = next(e for e in logs if e["event"] == "adequacao_conforme_recusado_por_carater_desconhecido")
    assert entry["tipo_carater"] == "' eletivo '"
    assert entry["tipo_carater"] != "eletivo", "must never normalize to look in-vocabulary"
    assert entry["motivo"] == _MOTIVO_CARATER


def test_m1_medidas_fabricadas_emite_log_com_os_placeholders() -> None:
    """Same auditability for blind spot (b): the log carries both the measurement and the
    placeholder it is indistinguishable from, so a reviewer can tell the two branches apart."""
    with structlog.testing.capture_logs() as logs:
        route_remediation(
            {
                **_M1_BASE,
                "tipo_carater": "eletivo",
                "tempo_acesso_apurado_min": 45,
                "distancia_apurada_km": 15.5,
            },
            dmn=_adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR"),
        )
    entry = next(e for e in logs if e["event"] == "adequacao_conforme_recusado_por_medidas_fabricadas")
    assert entry["tempo_acesso_apurado_min"] == 45
    assert entry["distancia_apurada_km"] == 15.5
    assert entry["tempo_placeholder_min"] == 45
    assert entry["distancia_placeholder_km"] == 15.5
    assert entry["motivo"] == _MOTIVO_PLACEHOLDER


def test_m1_cadeia_real_measure_gap_para_route_remediation() -> None:
    """END TO END through the two workers, with NOTHING seeded — the exact production shape that
    produced the blind spot: `measure_gap` fabricates 45/15.5, the process carries no
    `tipo_carater`, the table says CONFORME. Pre-fix this published conformity with no monitoring
    plan and no alert; now it routes to a human with the verdict preserved."""
    fatos = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert fatos["tempo_acesso_apurado_min"] == 45
    assert fatos["distancia_apurada_km"] == 15.5

    result = route_remediation(
        {**fatos, "dados_geo_completos": True},
        dmn=_adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR"),
    )
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"
    assert result["motivo"] == _MOTIVO_CARATER


def test_m1_cadeia_real_medidas_fabricadas_com_carater_declarado() -> None:
    """The SAME chain with a properly declared `tipo_carater` — so blind spot (a) cannot be what
    catches it. Nothing typed is seeded, `measure_gap` fabricates the pair, and the refusal comes
    from blind spot (b) alone: a CONFORME resting on numbers nobody measured."""
    fatos = measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia", "tipo_carater": "eletivo"})
    result = route_remediation(
        {**fatos, "tipo_carater": "eletivo", "dados_geo_completos": True},
        dmn=_adequacao_fake(gap_adequacao="CONFORME", roteamento_remediacao="MONITORAR"),
    )
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"
    assert result["motivo"] == _MOTIVO_PLACEHOLDER


# ---------------------------------------------------------------
# measure_gap — M-1: fabricated measurements are no longer silent
# ---------------------------------------------------------------

_EVENTO_FABRICADAS = "adequacao_medidas_fabricadas"


def test_measure_gap_avisa_quando_fabrica_as_duas_medidas() -> None:
    with structlog.testing.capture_logs() as logs:
        measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    entry = next(e for e in logs if e["event"] == _EVENTO_FABRICADAS)
    assert entry["tempo_fabricado"] is True
    assert entry["distancia_fabricada"] is True
    assert entry["log_level"] == "warning"


@pytest.mark.parametrize(
    ("seed", "tempo_fabricado", "distancia_fabricada"),
    [
        ({"tempo_acesso_apurado_min": 20}, False, True),
        ({"distancia_apurada_km": 3.5}, True, False),
    ],
)
def test_measure_gap_avisa_qual_das_duas_medidas_fabricou(
    seed: dict[str, Any], tempo_fabricado: bool, distancia_fabricada: bool
) -> None:
    """The warning must DISCRIMINATE — a flag that is always `True` would carry no information."""
    with structlog.testing.capture_logs() as logs:
        measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia", **seed})
    entry = next(e for e in logs if e["event"] == _EVENTO_FABRICADAS)
    assert entry["tempo_fabricado"] is tempo_fabricado
    assert entry["distancia_fabricada"] is distancia_fabricada


def test_measure_gap_silencioso_quando_ambas_as_medidas_sao_fatos_semeados() -> None:
    """NON-VACUITY: a warning that always fires would be noise, not evidence."""
    with structlog.testing.capture_logs() as logs:
        result = measure_gap(
            {
                "regiao_saude": "R-001",
                "especialidade": "cardiologia",
                "tempo_acesso_apurado_min": 20,
                "distancia_apurada_km": 3.5,
            }
        )
    assert [e for e in logs if e["event"] == _EVENTO_FABRICADAS] == []
    assert result["tempo_acesso_apurado_min"] == 20
    assert result["distancia_apurada_km"] == 3.5


def test_measure_gap_nao_declara_variavel_de_processo_nova() -> None:
    """MECHANISM PIN (M-1). The fabricated-measurement signal is an operator LOG line, NOT an
    engine variable: minting one would add an undeclared variable to a contracted process, whose
    variable tables are human-gated (SP-OP-ADEQUACAO-001.md:100-113 entrada, :123-133 saida;
    same call already disclosed in pagto.py:318-326). The returned key set must stay exactly the
    five facts the contract declares."""
    assert set(measure_gap({"regiao_saude": "R-001", "especialidade": "cardiologia"})) == {
        "tempo_acesso_apurado_min",
        "distancia_apurada_km",
        "prestadores_disponiveis",
        "cobertura_geo_suficiente",
        "dados_geo_completos",
    }


# ---------------------------------------------------------------
# notify_coordenacao
# ---------------------------------------------------------------


def test_notify_coordenacao_returns_no_fabricated_fact() -> None:
    """GAP-FAB-NOTIF fix: must not resurrect the fabricated `notificacao_enviada=True`
    constant (zero consumers in BPMN/DMN, not part of the contract's output-variable set)."""
    result = notify_coordenacao(
        {
            "regiao_saude": "R-001",
            "gap_adequacao": "GAP_CRITICO",
        }
    )
    assert result == {}
    assert "notificacao_enviada" not in result


def test_notify_coordenacao_missing_fields_does_not_raise() -> None:
    result = notify_coordenacao({})
    assert result == {}


# ---------------------------------------------------------------
# execute_remediation
# ---------------------------------------------------------------


def test_execute_remediation() -> None:
    result = execute_remediation(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
        }
    )
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == "SP-OP-CRED-001"


# ---------------------------------------------------------------
# update_monitoring_plan — NEUTRAL, no adverse effect (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_update_monitoring_plan_neutral() -> None:
    result = update_monitoring_plan(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "gap_adequacao": "GAP_LEVE",
        }
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"
    assert result["gap_adequacao"] == "GAP_LEVE"


def test_update_monitoring_plan_never_commits_fallback() -> None:
    """Monitoring/alert only -- NEVER commits cash-flow nor denies care (BPMN task doc,
    ST_UpdateMonitoringPlanL3: 'NAO compromete caixa nem nega atendimento')."""
    result = update_monitoring_plan({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    forbidden = {"COMPROMISSO_FALLBACK", "NEGADO", "NEGAR"}
    for value in result.values():
        assert str(value).upper() not in forbidden
    assert "decisao_remediacao" not in result
    assert "compromisso_fallback_registrado" not in result


# ---------------------------------------------------------------
# make_update_monitoring_plan_handler — item-9 notify-wiring fix: `register_adequacao_workers`
# used to `del kafka  # unused`; this raw handler publishes a `_NOTIFICATIONS_TOPIC` notification.
# ---------------------------------------------------------------


def _monitoring_plan_task(variables: dict) -> ExternalTask:
    return ExternalTask(
        task_id="et-mp-1",
        topic="operadora.adequacao.update_monitoring_plan",
        process_instance_id="pi-1",
        business_key="ADEQ-amh-R-001-cardiologia-2026-Q3",
        worker_id="w-1",
        variables=variables,
    )


async def test_make_update_monitoring_plan_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_update_monitoring_plan_handler(kafka)
    task = _monitoring_plan_task(
        {
            "tenant_id": "amh",
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "gap_adequacao": "GAP_LEVE",
        }
    )

    result = await handler(task)

    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload == {
        "type": "adequacao.update_monitoring_plan",
        "tenant_id": "amh",
        "regiao_saude": "R-001",
        "especialidade": "cardiologia",
        "gap_adequacao": "GAP_LEVE",
    }
    assert key == task.business_key
    assert kafka.best_effort_calls == [False]


async def test_make_update_monitoring_plan_handler_payload_is_non_phi() -> None:
    """Payload carries only cell identity (tenant/regiao/especialidade) + gap classification --
    no beneficiary identifier anywhere (this process carries none at all, ADR-0006)."""
    kafka = FakeKafkaPublisher()
    handler = make_update_monitoring_plan_handler(kafka)
    await handler(
        _monitoring_plan_task(
            {
                "tenant_id": "amh",
                "regiao_saude": "R-001",
                "especialidade": "cardiologia",
                "gap_adequacao": "GAP_CRITICO",
            }
        )
    )
    _, payload, _ = kafka.published[0]
    assert set(payload.keys()) == {"type", "tenant_id", "regiao_saude", "especialidade", "gap_adequacao"}


async def test_make_update_monitoring_plan_handler_no_producer_still_completes() -> None:
    """`kafka=None` -- no producer wired -- logs a warning and the task STILL completes; the L3
    monitoring-plan update itself is never blocked by a missing producer."""
    handler = make_update_monitoring_plan_handler(None)
    result = await handler(
        _monitoring_plan_task(
            {"regiao_saude": "R-001", "especialidade": "cardiologia", "gap_adequacao": "GAP_LEVE"}
        )
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert result["celula"] == "R-001:cardiologia"


# ---------------------------------------------------------------
# notify_sla_risk — informational, never adverse (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_notify_sla_risk_informational() -> None:
    result = notify_sla_risk({"regiao_saude": "R-001", "especialidade": "cardiologia"})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-rede"
    assert result["regiao_saude"] == "R-001"
    assert result["especialidade"] == "cardiologia"


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates the fallback commitment
    decision -- UT_DecisaoFallback stays open, the commitment NEVER arises from a timer."""
    result = notify_sla_risk({"regiao_saude": "R-001", "decisao_remediacao": "COMPROMISSO_FALLBACK"})
    assert "decisao_remediacao" not in result
    forbidden = {"COMPROMISSO_FALLBACK"}
    for value in result.values():
        assert str(value).upper() not in forbidden


# ---------------------------------------------------------------
# register_adequacao_workers — registration + topic-registry drift guard
# ---------------------------------------------------------------


def test_register_adequacao_workers_registers_new_topics() -> None:
    """`update_monitoring_plan`/`notify_sla_risk` are registered on their exact BPMN-declared
    topics -- closes 2 of the 3 registry-drift gaps documented in
    tests/integration/processes/test_sp_op_adequacao_001.py (FINDING 1);
    `prepare_remediation_dossier` is now the REAL Andre A2A raw async handler (DL-0033 closed) —
    registered via `harness.register()` (NOT the WorkerRegistry), topic always served.
    `update_monitoring_plan` is ALSO now a raw handler (item-9 notify-wiring fix — the
    `del kafka # unused` gap) — registers with `kafka=None` too, so the topic is always served
    even with no producer wired."""
    harness = WorkerHarness(None, worker_id="unit-test-adequacao")  # type: ignore[arg-type]
    register_adequacao_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.adequacao.update_monitoring_plan" in topics
    assert "operadora.adequacao.notify_sla_risk" in topics
    assert "operadora.adequacao.prepare_remediation_dossier" in topics
    assert harness.registry.get("operadora.adequacao.prepare_remediation_dossier") is None  # raw
    assert harness.registry.get("operadora.adequacao.update_monitoring_plan") is None  # raw (item-9)
    assert harness.registry.get("operadora.adequacao.notify_sla_risk") is not None  # still FunctionWorker
    adequacao_topics = {t for t in topics if t.startswith("operadora.adequacao.")}
    assert len(adequacao_topics) == 8  # all 8 BPMN-declared topics registered (dossier now REAL A2A)


async def test_register_adequacao_workers_update_monitoring_plan_publishes_via_wired_kafka() -> None:
    """End-to-end registration-level pin: `register_adequacao_workers(harness, kafka)` wires the
    SAME kafka producer into `update_monitoring_plan`'s raw handler -- dispatching the registered
    handler for the topic actually publishes (not just a unit-level check on the factory)."""
    harness = WorkerHarness(None, worker_id="unit-test-adequacao")  # type: ignore[arg-type]
    kafka = FakeKafkaPublisher()
    register_adequacao_workers(harness, kafka, dmn=FakeDmnTransport())
    handler = harness._handlers["operadora.adequacao.update_monitoring_plan"]  # type: ignore[attr-defined]  # raw-handler registry, test introspection
    result = await handler(
        _monitoring_plan_task(
            {"regiao_saude": "R-001", "especialidade": "cardiologia", "gap_adequacao": "GAP_LEVE"}
        )
    )
    assert result["plano_monitoramento_atualizado"] is True
    assert len(kafka.published) == 1
    assert kafka.published[0][1]["type"] == "adequacao.update_monitoring_plan"


# ---------------------------------------------------------------
# prepare_remediation_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed,
# DL-0037)
# ---------------------------------------------------------------


class _FakeDossierDispatcher:
    """Records the envelope; returns a programmed `DelegationResult` (or raises)."""

    def __init__(self, result: DelegationResult | None = None, exc: Exception | None = None) -> None:
        self.envelopes: list = []
        self._result = result
        self._exc = exc

    async def delegate(self, envelope) -> DelegationResult:  # noqa: ANN001 — duck-typed fake
        self.envelopes.append(envelope)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _dossier_task(variables: dict) -> ExternalTask:
    return ExternalTask(
        task_id="et-1",
        topic="operadora.adequacao.prepare_remediation_dossier",
        process_instance_id="pi-1",
        business_key="ADEQ-amh-SP-01-cardiologia",
        worker_id="w-1",
        variables=variables,
    )


_ADEQ_DOSSIER_VARS = {
    "tenant_id": "amh",
    "regiao_saude": "SP-01",
    "especialidade": "cardiologia",
    "gap_adequacao": "GAP_CRITICO",
    "roteamento_remediacao": "ANALISE_HUMANA",
}


async def test_prepare_remediation_dossier_delegates_to_andre_and_returns_real_outputs() -> None:
    """Dispatcher present -> await delegate -> dossier-real outputs: `dossier_prepared=True`,
    the `output_ref` reference, and Andre's agent-produced compact summary AS-IS
    (`dossier_summary` — the SME/PO-pending UT-form seam). The envelope uses the SHARED
    `analytics.population` type with the `adequacao-worker` origin (his flow disambiguator) and
    the cell key as task_id (Guard 4)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "ADEQ-amh-SP-01-cardiologia",
            "process://ADEQ-amh-SP-01-cardiologia",
            meta={"route": "human_review", "grupo_destino": "gestao-rede"},
        )
    )
    handler = make_prepare_remediation_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))

    assert result is not None
    assert result["dossier_prepared"] is True
    assert result["dossier_ref"] == "process://ADEQ-amh-SP-01-cardiologia"
    assert result["dossier_summary"] == {"route": "human_review", "grupo_destino": "gestao-rede"}
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "ADEQ-amh-SP-01-cardiologia"
    assert envelope.task_type == "analytics.population"
    assert envelope.origin == "adequacao-worker"
    assert envelope.target == "andre"
    assert envelope.payload_meta["gap_adequacao"] == "GAP_CRITICO"


async def test_prepare_remediation_dossier_without_dispatcher_fail_neutrals_with_gap() -> None:
    """DL-0037 degradation posture: dispatcher absent (degraded runtime) -> the task still
    COMPLETES (UT_DecisaoFallback must open) with `dossier_prepared=False` + the bounded gap
    token — never a raise, never a fabricated dossier."""
    handler = make_prepare_remediation_dossier_handler(None)
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "dispatcher_unavailable"


async def test_prepare_remediation_dossier_missing_cell_identity_gap_never_delegates() -> None:
    """No tenant/regiao/especialidade (incl. whitespace-only, `non_blank` discipline) -> no
    degenerate ADEQ task_id is ever delegated; gap marker, UT still opens."""
    dispatcher = _FakeDossierDispatcher(result=DelegationResult.ok("x", "process://x"))
    handler = make_prepare_remediation_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task({"tenant_id": "amh", "regiao_saude": "  "}))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "missing_business_identifiers"
    assert dispatcher.envelopes == []


async def test_prepare_remediation_dossier_delegation_failure_fail_neutrals_never_raises() -> None:
    """ANY delegation exception -> gap marker + loud log; raw error text stays OUT of the engine
    variables (bounded class token only)."""
    handler = make_prepare_remediation_dossier_handler(
        _FakeDossierDispatcher(exc=RuntimeError("pg down: dsn=secret"))  # type: ignore[arg-type]
    )
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_failed"
    assert "secret" not in str(result.values())


async def test_prepare_remediation_dossier_structured_rejection_gap_bounded_reason() -> None:
    handler = make_prepare_remediation_dossier_handler(
        _FakeDossierDispatcher(  # type: ignore[arg-type]
            result=DelegationResult.rejected(
                "ADEQ-amh-SP-01-cardiologia",
                RejectionReason.TASK_TYPE_NOT_ACCEPTED,
                detail="not accepted",
            )
        )
    )
    result = await handler(_dossier_task(_ADEQ_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_rejected:task_type_not_accepted"


# ---------------------------------------------------------------
# register_fallback_commitment — GUARD
# ---------------------------------------------------------------


def test_fallback_commitment_happy_path() -> None:
    result = register_fallback_commitment(
        {
            "decisao_remediacao": "COMPROMISSO_FALLBACK",
            "tipo_fallback": "livre_escolha",
            "justificativa_fallback": "Sem prestador na regiao — RN 259",
            "referencia_regulatoria": "RN 259",
            "responsavel_id": "gestor-001",
            "estimativa_custo_cents": 500000,
        }
    )
    assert result["compromisso_fallback_registrado"] is True


def test_fallback_commitment_rejects_wrong_decisao() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "MONITORAR_OK",
                "tipo_fallback": "",
                "justificativa_fallback": "",
                "referencia_regulatoria": "",
                "responsavel_id": "",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "MONITORAR_OK" in excinfo.value.message


def test_fallback_commitment_rejects_missing_justificativa() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "COMPROMISSO_FALLBACK",
                "tipo_fallback": "reembolso_garantido",
                "justificativa_fallback": "",
                "referencia_regulatoria": "RN 259",
                "responsavel_id": "gestor-001",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN


# ---------------------------------------------------------------
# register_fallback_commitment — whitespace-bypass vectors (t3.1-guard-input-hardening,
# closing the #133-audit-flagged gap at adequacao.py:289-295: the guard's original bare
# `if not tipo_fallback` / `if not justificativa` / ... checks let WHITESPACE-ONLY decision +
# accountability fields through, the same class the c1377fa fix closed for
# pagto.register_payment_refusal. Every vector below MUST refuse with
# ERR_FALLBACK_COMMITMENT_NOT_HUMAN -- whitespace-only is the SAME as absent (ADR-0007/RN 259:
# a fallback commitment must carry an identifying human approver + a real justification).
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]


def _fallback_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_fallback_commitment -- so any guard failure observed
    in a test is attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_remediacao": "COMPROMISSO_FALLBACK",
        "tipo_fallback": "livre_escolha",
        "justificativa_fallback": "Sem prestador na regiao — RN 259",
        "referencia_regulatoria": "RN 259",
        "responsavel_id": "gestor-001",
        "estimativa_custo_cents": 500000,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_tipo_fallback_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(tipo_fallback=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "tipo_fallback" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_tipo_fallback_refuses(non_string: object) -> None:
    """A NON-string tipo_fallback normalizes to '' and refuses -- the pre-fix bare truthiness
    check (`if not tipo_fallback`) would have silently PASSED a truthy non-string."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(tipo_fallback=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "tipo_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_justificativa_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(justificativa_fallback=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_justificativa_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(justificativa_fallback=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_referencia_regulatoria_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(referencia_regulatoria=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "referencia_regulatoria" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_referencia_regulatoria_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(referencia_regulatoria=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "referencia_regulatoria" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_whitespace_only_responsavel_id_refuses(whitespace: str) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(responsavel_id=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fallback_commitment_non_string_responsavel_id_refuses(non_string: object) -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(responsavel_id=non_string))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fallback_commitment_both_responsavel_and_justificativa_whitespace_refuses(
    whitespace: str,
) -> None:
    """Both responsavel_id AND justificativa_fallback whitespace-only -- both missing fields
    named in the guard's error message."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            _fallback_baseline(responsavel_id=whitespace, justificativa_fallback=whitespace)
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message
    assert "justificativa_fallback" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_fallback_commitment_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_remediacao normalizes to '' -> != COMPROMISSO_FALLBACK ->
    refuses (the engine's own gateway default routing is not itself a human decision)."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(decisao_remediacao=whitespace))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "decisao_remediacao" in excinfo.value.message


def test_fallback_commitment_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and
    still register (pins the normalization behavior -- this is NOT a bypass, it is the
    documented, intentional consequence of `.strip()`)."""
    result = register_fallback_commitment(
        _fallback_baseline(
            decisao_remediacao=" COMPROMISSO_FALLBACK ",
            tipo_fallback=" livre_escolha ",
            justificativa_fallback=" Sem prestador na regiao — RN 259 ",
            referencia_regulatoria=" RN 259 ",
            responsavel_id=" gestor-001 ",
        )
    )
    assert result["compromisso_fallback_registrado"] is True


@pytest.mark.parametrize(
    "decision",
    [
        "compromisso_fallback",
        "Compromisso_Fallback",
        "COMPROMISSO_FALLBACK_X",
        "XCOMPROMISSO_FALLBACK",
        "MONITORAR_OK ",
    ],
)
def test_fallback_commitment_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings of the decision
    literal never satisfy the guard."""
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(_fallback_baseline(decisao_remediacao=decision))
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
