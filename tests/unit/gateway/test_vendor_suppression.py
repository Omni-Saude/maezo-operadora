"""GP11 wiring — taxonomia C1–C6, calendário own-code, egresso k-anon e KPI (VW4, OP20).

Corações do pacote (§GP11 do ANSWERS-V1, insumos ACEITOS pelo dono 2026-10-07, sha ab262f7b…):

- **Negativos (o firewall é a recusa — §4e critério 4):** C1/C3/C4 no payload ⇒ recusa tipada
  do egresso inteiro E o KPI `phi_egress_violations` PERMANECE 0 (recusa = contenção — violação
  é o que PASSOU a fronteira); classe desconhecida ⇒ recusa (OP20 §3); célula C2/C6 < k=100 ⇒
  LINHA suprimida (e contada); audiência < 300 ⇒ exportação INTEIRA recusada; store ausente
  ⇒ `SOURCE_UNAVAILABLE` (UNKNOWN ≠ conjunto vazio).
- **Positivos:** calendário own-code seg–sex SEM feriados (limite documentado) produz o
  relógio 50/80/100 determinístico; célula ≥ k passa; lista constrói COM honra preventiva;
  KPI computado (nunca constante) e moldável.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from maezo.gateway.vendor_suppression import (
    EGRESS_AUDIENCE_GATE_MINIMUM,
    K_ANON_FLOOR,
    SLA_BUSINESS_DAYS,
    DeclaredField,
    EgressLine,
    EscalationClock,
    FieldClass,
    PhiEgressViolationClass,
    SuppressionCommand,
    SuppressionEgressAttempt,
    SuppressionEgressTelemetry,
    SuppressionError,
    SuppressionKey,
    SuppressionRefusalReason,
    add_business_days,
    build_contact_list,
    prepare_egress_export,
    suppression_ref_of,
)

_T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)  # quarta-feira


def _key(n: str) -> SuppressionKey:
    return SuppressionKey(subject_ref=f"subj-{n}", contact_channel="chan-A")


def _line(n: str, tuple_value: tuple[str, ...], *fields: DeclaredField) -> EgressLine:
    return EgressLine(
        key=_key(n),
        fields=fields or (DeclaredField("f", FieldClass.C5_COMPORTAMENTAL),),
        tuple_value=tuple_value,
    )


# ----------------------------------------------------------------------------------
# Calendário own-code seg–sex — SEM feriados (limite documentado) e relógio 50/80/100
# ----------------------------------------------------------------------------------


def test_o_calendario_pula_fim_de_semana_e_nao_conhece_feriado() -> None:
    """§4c: dias úteis = seg–sex. Sexta + 1 dia útil = segunda; o calendário own-code NÃO
    observa feriado (limite documentado — feriados exigiriam tabela custodiada RATIFY-LATER
    e ninguém a inventa aqui)."""
    friday = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)  # sexta-feira
    assert friday.weekday() == 4
    monday = add_business_days(friday, 1)
    assert monday.weekday() == 0
    assert (monday - friday).days == 3  # sex -> seg pula o fim de semana
    # Deadline sempre cai em dia útil.
    assert EscalationClock.compute(friday).t_deadline.weekday() < 5


def test_o_relogio_do_registro_nasce_deterministico_50_80_100() -> None:
    clock = EscalationClock.compute(_T0)
    assert clock.t_lembrete <= clock.t_escalonado < clock.t_deadline
    assert _business_days(_T0, clock.t_deadline) == SLA_BUSINESS_DAYS  # 100% = 15º dia útil
    assert _business_days(_T0, clock.t_escalonado) == 12  # 80% = 12º exato (Fraction)
    assert _business_days(_T0, clock.t_lembrete) == 7  # 50% = 7º (piso de 7,5 — nunca depois)


def _business_days(start: datetime, end: datetime) -> int:
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def test_relogio_recusa_datetime_naive() -> None:
    with pytest.raises(SuppressionError) as exc:
        EscalationClock.compute(datetime(2026, 10, 7, 10, 0))  # naive
    assert exc.value.reason is SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE


def test_o_digest_da_business_key_e_deterministico_e_projeta_o_par() -> None:
    assert suppression_ref_of("subj-1", "chan-A") == suppression_ref_of("subj-1", "chan-A")
    assert suppression_ref_of("subj-1", "chan-A") != suppression_ref_of("subj-1", "chan-B")
    assert suppression_ref_of("subj-1", "chan-A") != suppression_ref_of("subj-2", "chan-A")
    assert len(suppression_ref_of("subj-1", "chan-A")) == 64


def test_o_comando_e_minimizado_art_10() -> None:
    command = SuppressionCommand(
        tenant="t-1",
        subject_ref="subj-1",
        contact_channel="chan-A",
        canal="whatsapp",
        categoria_sujeito="vendedor",
        motivo="oposicao art. 18 §2o",
    )
    assert command.suppression_ref == suppression_ref_of("subj-1", "chan-A")
    with pytest.raises(ValidationError):
        SuppressionCommand.model_validate(
            {
                "tenant": "t-1",
                "subject_ref": "subj-1",
                "contact_channel": "chan-A",
                "canal": "whatsapp",
                "categoria_sujeito": "desconhecido",  # fora do Literal fechado
                "motivo": "x",
            }
        )


# ----------------------------------------------------------------------------------
# Egresso k-anon (§4a/§4b) — recusa tipada ≠ violação (§4e critério 4)
# ----------------------------------------------------------------------------------


def test_c1_em_payload_recusa_o_egresso_inteiro_e_o_kpi_permanece_zero() -> None:
    """§4e critério 4 verbatim: recusa tipada = contenção, NÃO violação — o KPI fica 0 e a
    TENTATIVA fica contada (presence-only, sem conteúdo)."""
    telemetry = SuppressionEgressTelemetry()
    lines = [_line("1", ("t-1",), DeclaredField("nome", FieldClass.C1_IDENTIFICATIVO))]
    with pytest.raises(SuppressionError) as exc:
        prepare_egress_export(lines, audience_size=EGRESS_AUDIENCE_GATE_MINIMUM, telemetry=telemetry)
    assert exc.value.reason is SuppressionRefusalReason.PHI_IN_COMMERCIAL_INPUT
    assert telemetry.phi_egress_violations == 0
    assert telemetry.window()[SuppressionEgressAttempt.EGRESS_REFUSED_PROHIBITED_CLASS.value] == 1


@pytest.mark.parametrize("classe", [FieldClass.C3_CLINICO_PROTEGIDO, FieldClass.C4_CLINICO_AREGREGADO])
def test_c3_e_c4_sao_proibidos_como_c1(classe: FieldClass) -> None:
    telemetry = SuppressionEgressTelemetry()
    lines = [_line("1", ("t",), DeclaredField("cid", classe))]
    with pytest.raises(SuppressionError) as exc:
        prepare_egress_export(lines, audience_size=EGRESS_AUDIENCE_GATE_MINIMUM, telemetry=telemetry)
    assert exc.value.reason is SuppressionRefusalReason.PHI_IN_COMMERCIAL_INPUT
    assert telemetry.phi_egress_violations == 0


def test_classe_desconhecida_recusa_op20_secao_3() -> None:
    telemetry = SuppressionEgressTelemetry()
    lines = [_line("1", ("t",), DeclaredField("campo", None))]
    with pytest.raises(SuppressionError) as exc:
        prepare_egress_export(lines, audience_size=EGRESS_AUDIENCE_GATE_MINIMUM, telemetry=telemetry)
    assert exc.value.reason is SuppressionRefusalReason.UNKNOWN_FIELD_CLASS


def test_celula_sub_k_suprime_a_linha_e_a_celula_cheia_passa() -> None:
    """Célula < k=100 ⇒ linha suprimida (o k-anon a funcionar — §4a); célula == k passa."""
    telemetry = SuppressionEgressTelemetry()
    below = [_line(f"b{i}", ("celula-pequena",)) for i in range(99)]  # 99 < k=100
    at_k = [_line(f"o{i}", (f"celula-cheia-{i % 4}",)) for i in range(4 * K_ANON_FLOOR)]  # 4x k
    export = prepare_egress_export(
        [*below, *at_k], audience_size=4 * K_ANON_FLOOR, telemetry=telemetry
    )
    assert export.suppressed_lines == 99
    assert all(line.key != _key("b0") for line in export.exported)
    assert len(export.exported) == 4 * K_ANON_FLOOR
    assert telemetry.window()[SuppressionEgressAttempt.EGRESS_LINE_SUPPRESSED_SUBK.value] == 99
    assert telemetry.phi_egress_violations == 0  # supressão é o k-anon FUNCIONANDO


def test_audiencia_abaixo_do_gate_recusa_a_exportacao_inteira() -> None:
    """§4a exemplo trabalhado: n=250 pós-supressão, 250 ≥ k — ATENDE linha; 250 < 300 — a
    exportação INTEIRA é recusada. Nunca se completa lista para fechar audiência."""
    telemetry = SuppressionEgressTelemetry()
    lines = [_line(f"{i}", (f"tupla-{i}",)) for i in range(250)]
    with pytest.raises(SuppressionError) as exc:
        prepare_egress_export(lines, audience_size=250, telemetry=telemetry)
    assert exc.value.reason is SuppressionRefusalReason.AUDIENCE_BELOW_GATE
    assert telemetry.window()[SuppressionEgressAttempt.EGRESS_REFUSED_AUDIENCE_GATE.value] == 1


def test_exportacao_viavel_passa_os_dois_gates() -> None:
    """Exemplo §4a completo: k e o piso de audiência não competem — k primeiro (MAEZO decide
    antes, sempre), audiência depois (o canal decide). 400 linhas em 4 células de 100 passa."""
    lines = [_line(f"{i}", (f"tupla-{i % 4}",)) for i in range(400)]
    export = prepare_egress_export(lines, audience_size=400)
    assert len(export.exported) == 400
    assert export.suppressed_lines == 0


def test_nenhuma_rate_atravessa_payload_estrutural_cade() -> None:
    """G-CADE (VW0-D23): telemetria presence-only — o formato fechado de linha de egresso não
    tem campo de medida/taxa/frequência; a AUSÊNCIA é o firewall (assert estrutural)."""
    assert set(EgressLine.__dataclass_fields__) == {"key", "fields", "tuple_value"}
    assert set(DeclaredField.__dataclass_fields__) == {"name", "field_class"}
    assert "rate" not in EgressLine.__dataclass_fields__
    assert "taxa" not in EgressLine.__dataclass_fields__


# ----------------------------------------------------------------------------------
# KPI §4e — computado, nunca constante; presença-only, nunca conteúdo
# ----------------------------------------------------------------------------------


def test_o_kpi_e_computado_e_vira_positivo_so_com_violacao_ou_corrupcao() -> None:
    telemetry = SuppressionEgressTelemetry()
    assert telemetry.phi_egress_violations == 0
    telemetry.record_violation(PhiEgressViolationClass.SUBK_CELL_EXPORTED)
    assert telemetry.phi_egress_violations == 1
    telemetry.record_violation(PhiEgressViolationClass.C1_PAST_BOUNDARY)
    assert telemetry.phi_egress_violations == 2
    telemetry.reset()
    assert telemetry.phi_egress_violations == 0
    # Corrupção do estado interno é acusada na MESMA varredura (molde #681).
    telemetry._violations["campo-fantasma"] = 1  # o teste CORROMPE de propósito
    assert telemetry.phi_egress_violations == 1


def test_string_solta_nunca_entraria_no_contador() -> None:
    telemetry = SuppressionEgressTelemetry()
    with pytest.raises(TypeError):
        telemetry.record_attempt("egress_refused_prohibited_class")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        telemetry.record_violation("c1_past_boundary")  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------
# Honra na construção de lista — UNKNOWN ≠ conjunto vazio
# ----------------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, keys: frozenset[SuppressionKey] | None, *, boom: bool = False) -> None:
        self.keys = keys
        self.boom = boom

    async def active_suppressions(self, tenant: str) -> frozenset[SuppressionKey]:
        del tenant
        if self.boom:
            raise RuntimeError("db down")
        assert self.keys is not None
        return self.keys


@pytest.mark.asyncio
async def test_store_ausente_recusa_e_nunca_trata_como_conjunto_vazio() -> None:
    telemetry = SuppressionEgressTelemetry()
    with pytest.raises(SuppressionError) as exc:
        await build_contact_list("t-1", [_key("1")], store=None, telemetry=telemetry)
    assert exc.value.reason is SuppressionRefusalReason.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_store_ilegivel_recusa_com_a_mesma_forma() -> None:
    with pytest.raises(SuppressionError) as exc:
        await build_contact_list("t-1", [_key("1")], store=_FakeStore(frozenset(), boom=True))
    assert exc.value.reason is SuppressionRefusalReason.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_linhas_de_sujeitos_suprimidos_nunca_entrarem_na_lista() -> None:
    suppressed = frozenset({_key("2"), _key("4")})
    candidates = [_key("1"), _key("2"), _key("3"), _key("4")]
    kept = await build_contact_list("t-1", candidates, store=_FakeStore(suppressed))
    assert kept == (_key("1"), _key("3"))


@pytest.mark.asyncio
async def test_a_remocao_na_construcao_e_contada_presence_only() -> None:
    telemetry = SuppressionEgressTelemetry()
    await build_contact_list(
        "t-1", [_key("1"), _key("2")], store=_FakeStore(frozenset({_key("2")})), telemetry=telemetry
    )
    assert telemetry.window()[SuppressionEgressAttempt.LIST_ROW_REMOVED.value] == 1
    assert telemetry.phi_egress_violations == 0


# --- Achado do guard-test-engineer: classe de TUPLA declarada sem tuple_value ---------------


def test_c2_declared_without_tuple_refuses_the_whole_egress_fail_closed() -> None:
    """§4b: k aplica-se à TUPLA — uma linha declarada C2/C6 SEM tupla deixaria o
    quase-identificador fora da contagem de célula (nunca suprimida). Recusa tipada
    CONTRACT_MISMATCH do egresso inteiro, com a tentativa contada no catálogo presence-only
    (contenção, nunca violação). Zero linhas exportadas."""
    from maezo.gateway.vendor_suppression import (
        DeclaredField,
        EgressLine,
        FieldClass,
        SuppressionEgressAttempt,
        SuppressionEgressTelemetry,
        SuppressionError,
        SuppressionRefusalReason,
        prepare_egress_export,
    )

    telemetry = SuppressionEgressTelemetry()
    line = EgressLine(
        key=_key("regressao-f1"),
        tuple_value=(),  # inconsistente: o campo declara C2 mas a tupla não veio
        fields=(DeclaredField("matricula", FieldClass.C2_QUASE_IDENTIFICADOR),),
    )
    with pytest.raises(SuppressionError) as exc:
        prepare_egress_export(
            (line,), audience_size=500, telemetry=telemetry
        )
    assert exc.value.reason is SuppressionRefusalReason.CONTRACT_MISMATCH
    assert telemetry._attempts[SuppressionEgressAttempt.EGRESS_REFUSED_TUPLE_CLASS_WITHOUT_TUPLE] == 1
    assert telemetry.phi_egress_violations == 0  # contenção ≠ violação (§4e critério 4)
