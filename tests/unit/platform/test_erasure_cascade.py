"""R-013 (F-008): a cascata de eliminacao LGPD DARK — cadeia com a flag ON, provas negativas.

O que estes testes travam, uma linha cada:

* a ordem dos portoes e carregada: legal-hold ANTES do plano, plano ANTES da ponte, ponte ANTES
  do pre-voo, pre-voo ANTES de qualquer executor — nenhum efeito antes do ultimo portao;
* legal-hold ativo OU duvida (registro ausente, titular ausente, forma invalida) = eliminacao
  DIFERIDA, executores intocados (ADR-0020 item 6, predicado R-044 reaproveitado, nao copiado);
* plano do DPO nao ratificado (o DRAFT enviado) = recusa; nenhuma decisao inventada;
* a ponte A (titular_pseudo_id -> fhir_patient_id) e obrigatoria e DARK: flag OFF ou divergencia
  = recusa;
* relacao que o plano manda eliminar sem executor, ou surrogate mpi_id<->fhir sem store =
  `ErasureNotImplementedError`, tudo-ou-nada;
* caminho feliz: decisao do DPO carregada ate o executor, `RETER_COM_BASE_LEGAL` respeitado,
  relacao RETIRADA ignorada, surrogate por ultimo, verificacao exige residuo zero;
* falha/residuo/forma invalida de executor = `partial`/`failed`, nunca `completed`;
* PHI: nenhum log, mensagem de recusa ou repr carrega o paciente ou o pseudonimo;
* dark por estrutura: `ErasureManager` sem chamador de producao; a flag fora de `deploy/**`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.platform import erasure as er
from maezo.platform import identity_bridges as ib
from maezo.platform.identity_bridges import BridgeResolution
from maezo.platform.lifecycle import erasure_plan as ep
from maezo.platform.lifecycle import legal_hold as lh

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_MAEZO = _REPO_ROOT / "src" / "maezo"

_TENANT = "tenant-fix"
_PATIENT = "pat-sentinel-must-not-leak-0013"
_PSEUDO = "pseudo-sentinel-must-not-leak-0013"
_SHA = "a" * 64


@pytest.fixture(autouse=True)
def _env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        er.CASCADE_FLAG_ENV,
        ib.TITULAR_BRIDGE_FLAG_ENV,
        ib.THREAD_BRIDGE_FLAG_ENV,
        ep.PLAN_PATH_ENV,
        lh.REGISTRY_PATH_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def _plan(decisions: dict[str, str] | None = None) -> ep.ErasurePlan:
    """Plano de FIXTURE (nenhuma decisao de DPO existe neste PR): tudo RETER, exceto o pedido."""
    chosen = decisions or {}
    by_table = {
        layer.tabela: ep.ErasureLayerDecision(
            camada=layer.camada,
            tabela=layer.tabela,
            decisao_dpo=chosen.get(layer.tabela, "RETER_COM_BASE_LEGAL"),
            base_legal="fixture value, not a legal opinion",
            retencao="fixture value",
        )
        for layer in ep.PERSISTENCE_LAYERS
    }
    return ep.ErasurePlan(
        dpo_reviewer="fixture",
        dpo_review_date="2026-01-01",
        evidence_ref="EL-FIXTURE-R013",
        review_packet="docs/reviews/adr-0029-erasure-packet.md",
        notes="fixture, unit test only",
        decisions=MappingProxyType(by_table),
    )


def _livre(**_: Any) -> lh.LegalHoldDecision:
    return lh.LegalHoldDecision(lh.LegalHoldOutcome.LIVRE, lh.MOTIVO_SEM_HOLD_ATIVO, registro_sha256=_SHA)


def _retido(**_: Any) -> lh.LegalHoldDecision:
    return lh.LegalHoldDecision(
        lh.LegalHoldOutcome.RETIDO, lh.MOTIVO_HOLD_ATIVO, hold_ids=("HOLD-1",), registro_sha256=_SHA
    )


class _Source:
    def __init__(self, patient: str = _PATIENT, tenant: str = _TENANT) -> None:
        self._resolution = BridgeResolution(tenant_id=tenant, fhir_patient_id=patient)
        self.calls = 0

    def resolve(self, tenant_id: str, reference: str) -> BridgeResolution | None:
        self.calls += 1
        return self._resolution


class _Executor:
    """Executor de teste: registra a ordem global das chamadas e simula residuo/falha."""

    def __init__(
        self,
        name: str,
        journal: list[str],
        *,
        rows: Any = 3,
        residual: Any = 0,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.journal = journal
        self.rows = rows
        self.residual = residual
        self.error = error
        self.decisions: list[str] = []

    def apply(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int:
        self.journal.append(f"apply:{self.name}")
        self.decisions.append(decisao_dpo)
        if self.error is not None:
            raise self.error
        return self.rows  # type: ignore[no-any-return]

    def remaining(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int:
        self.journal.append(f"remaining:{self.name}")
        return self.residual  # type: ignore[no-any-return]


class _Surrogate:
    def __init__(self, journal: list[str], *, residual: int = 0, error: Exception | None = None) -> None:
        self.journal = journal
        self.residual = residual
        self.error = error

    def drop(self, tenant_id: str, fhir_patient_id: str) -> int:
        self.journal.append("drop:surrogate")
        if self.error is not None:
            raise self.error
        return 1

    def remaining(self, tenant_id: str, fhir_patient_id: str) -> int:
        self.journal.append("remaining:surrogate")
        return self.residual


def _manager(
    journal: list[str],
    *,
    plan: ep.ErasurePlan | None = None,
    executors: dict[str, Any] | None = None,
    surrogate: Any = "default",
    source: Any = "default",
    evaluator: Any = _livre,
    bridge_enabled: bool | None = True,
) -> er.ErasureManager:
    return er.ErasureManager(
        executors=executors if executors is not None else {},
        surrogate_store=_Surrogate(journal) if surrogate == "default" else surrogate,
        titular_bridge_source=_Source() if source == "default" else source,
        titular_bridge_enabled=bridge_enabled,
        plan=plan,
        legal_hold_evaluator=evaluator,
        enabled=True,
    )


def _eliminar_episodica(journal: list[str], **kw: Any) -> tuple[ep.ErasurePlan, dict[str, _Executor]]:
    plan = _plan({"agent_memory": "ELIMINAR", "erasure_log": "ANONIMIZAR"})
    executors = {
        "agent_memory": _Executor("agent_memory", journal, **kw),
        "erasure_log": _Executor("erasure_log", journal),
    }
    return plan, executors


# ---------------------------------------------------------------------------
# Legal-hold (ADR-0020 item 6 / R-044) — antes de tudo
# ---------------------------------------------------------------------------


def test_legal_hold_ativo_difere_sem_tocar_plano_nem_executor() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    loads: list[int] = []

    def loader() -> ep.ErasurePlan:
        loads.append(1)
        return plan

    manager = er.ErasureManager(
        executors=executors,
        surrogate_store=_Surrogate(journal),
        titular_bridge_source=_Source(),
        titular_bridge_enabled=True,
        plan_loader=loader,
        legal_hold_evaluator=_retido,
        enabled=True,
    )
    with pytest.raises(er.ErasureLegalHoldDeferredError) as exc:
        manager.erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.reason == er.REASON_LEGAL_HOLD_DEFERRED
    assert exc.value.decision.outcome is lh.LegalHoldOutcome.RETIDO
    assert journal == [] and loads == [], "hold ativo: nem plano nem executor podem ser tocados"


def test_legal_hold_real_com_registro_yaml_retem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Integra o predicado REAL (R-044, nao uma copia): hold ativo no registro => diferido."""
    registry = tmp_path / "holds.yaml"
    registry.write_text(
        "schema_version: 1\nunratified: false\nholds:\n"
        f"  - hold_id: CASO-1\n    tenant_id: {_TENANT}\n    titular_pseudo_id: {_PSEUDO}\n"
        "    colocado_em: 2026-01-01\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(lh.REGISTRY_PATH_ENV, str(registry))
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    manager = er.ErasureManager(
        executors=executors,
        surrogate_store=_Surrogate(journal),
        titular_bridge_source=_Source(),
        titular_bridge_enabled=True,
        plan=plan,
        enabled=True,
    )
    with pytest.raises(er.ErasureLegalHoldDeferredError) as exc:
        manager.erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.decision.hold_ids == ("CASO-1",)
    assert journal == []


def test_registro_de_legal_hold_ausente_e_duvida_que_difere() -> None:
    """Predicado REAL sem registro configurado => INDETERMINADO => diferido (fail-closed)."""
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    manager = er.ErasureManager(
        executors=executors,
        surrogate_store=_Surrogate(journal),
        titular_bridge_source=_Source(),
        titular_bridge_enabled=True,
        plan=plan,
        enabled=True,
    )
    with pytest.raises(er.ErasureLegalHoldDeferredError) as exc:
        manager.erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.decision.outcome is lh.LegalHoldOutcome.INDETERMINADO
    assert journal == []


def test_sem_titular_o_predicado_real_difere() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    manager = er.ErasureManager(
        executors=executors, surrogate_store=_Surrogate(journal), plan=plan, enabled=True
    )
    with pytest.raises(er.ErasureLegalHoldDeferredError) as exc:
        manager.erase(_TENANT, _PATIENT)
    assert exc.value.decision.motivo == lh.MOTIVO_TITULAR_NAO_IDENTIFICADO
    assert journal == []


def test_avaliador_injetado_nao_transforma_titular_ausente_em_livre() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors).erase(_TENANT, _PATIENT, titular_pseudo_id=" ")
    assert exc.value.reason == er.REASON_SUBJECT_BLANK
    assert journal == []


def test_predicado_com_forma_invalida_e_recusa() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors, evaluator=lambda **_: "LIVRE").erase(
            _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
        )
    assert exc.value.reason == er.REASON_LEGAL_HOLD_DEFERRED
    assert journal == []


@pytest.mark.parametrize(
    ("tenant", "patient"), [("", _PATIENT), (_TENANT, ""), (" t", _PATIENT), (None, _PATIENT)]
)
def test_insumo_em_branco_e_recusa(tenant: Any, patient: Any) -> None:
    journal: list[str] = []
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal).erase(tenant, patient, titular_pseudo_id=_PSEUDO)
    assert exc.value.reason in {er.REASON_TENANT_BLANK, er.REASON_SUBJECT_BLANK}
    assert journal == []


# ---------------------------------------------------------------------------
# Plano do DPO (Chave A) — campos do DPO seguem em branco no DRAFT enviado
# ---------------------------------------------------------------------------


def test_plano_draft_enviado_recusa_com_motivo_do_carregador() -> None:
    """O DRAFT de `spec/policies/retention/erasure-plan.template.yaml` (DPO em branco) recusa."""
    journal: list[str] = []
    executors = {"agent_memory": _Executor("agent_memory", journal)}
    manager = er.ErasureManager(
        executors=executors,
        surrogate_store=_Surrogate(journal),
        titular_bridge_source=_Source(),
        titular_bridge_enabled=True,
        legal_hold_evaluator=_livre,
        enabled=True,
    )
    with pytest.raises(er.ErasureRefusedError) as exc:
        manager.erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.reason == er.REASON_PLAN_UNAVAILABLE
    assert isinstance(exc.value.__cause__, ep.ErasurePlanUnavailableError)
    assert exc.value.__cause__.reason == ep.REASON_NOT_RATIFIED
    assert journal == []


# ---------------------------------------------------------------------------
# Ponte A (titular_pseudo_id -> fhir_patient_id), reaproveitada de #965
# ---------------------------------------------------------------------------


def test_ponte_titular_com_flag_off_recusa() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    source = _Source()
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors, source=source, bridge_enabled=None).erase(
            _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
        )
    assert exc.value.reason == er.REASON_IDENTITY_UNCONFIRMED
    assert isinstance(exc.value.__cause__, ib.ErasureBridgeRefusedError)
    assert exc.value.__cause__.reason == ib.REASON_FLAG_OFF
    assert source.calls == 0 and journal == []


def test_ponte_sem_fonte_recusa() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors, source=None).erase(
            _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
        )
    assert exc.value.reason == er.REASON_IDENTITY_UNCONFIRMED
    assert journal == []


def test_ponte_resolvendo_outro_paciente_e_divergencia() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors, source=_Source(patient="outro")).erase(
            _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
        )
    assert exc.value.reason == er.REASON_IDENTITY_DIVERGENCE
    assert journal == []


# ---------------------------------------------------------------------------
# Pre-voo: executores e surrogate (ADR-0019 §5) — tudo-ou-nada
# ---------------------------------------------------------------------------


def test_relacao_a_eliminar_sem_executor_e_not_implemented_sem_efeito() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    del executors["erasure_log"]
    with pytest.raises(er.ErasureNotImplementedError) as exc:
        _manager(journal, plan=plan, executors=executors).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.reason == er.REASON_EXECUTOR_UNBOUND
    assert "erasure_log" in exc.value.detail
    assert journal == [], "pre-voo e tudo-ou-nada: agent_memory NAO pode ter sido aplicada"


def test_surrogate_mpi_id_sem_store_e_not_implemented_sem_efeito() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    with capture_logs() as captured, pytest.raises(er.ErasureNotImplementedError) as exc:
        _manager(journal, plan=plan, executors=executors, surrogate=None).erase(
            _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
        )
    assert exc.value.reason == er.REASON_SURROGATE_UNBOUND
    assert "ADR-0019" in exc.value.detail, "a recusa do PRE-VOO (nao a defesa tardia) nomeia a ADR"
    assert any(e.get("reason") == er.REASON_SURROGATE_UNBOUND for e in captured)
    assert journal == []


def test_executor_para_relacao_desconhecida_e_recusa() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    executors["tabela_inventada"] = _Executor("x", journal)
    with pytest.raises(er.ErasureRefusedError) as exc:
        _manager(journal, plan=plan, executors=executors).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert exc.value.reason == er.REASON_EXECUTOR_UNKNOWN_RELATION
    assert journal == []


# ---------------------------------------------------------------------------
# Execucao e verificacao
# ---------------------------------------------------------------------------


def test_caminho_feliz_carrega_a_decisao_do_dpo_e_verifica() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )

    assert result.status == er.STATUS_COMPLETED
    assert journal == [
        "apply:agent_memory",
        "apply:erasure_log",
        "drop:surrogate",
        "remaining:agent_memory",
        "remaining:erasure_log",
        "remaining:surrogate",
    ], "ordem do plano, surrogate por ultimo, verificacao depois"
    assert executors["agent_memory"].decisions == ["ELIMINAR"]
    assert executors["erasure_log"].decisions == ["ANONIMIZAR"]
    assert result.episodic_erased is True
    assert result.working_erased is False, "nenhuma relacao de trabalho foi acionada pelo plano"
    assert result.semantic_erased is False, "camada semantica suspensa (ADR-0047)"
    assert result.surrogate_dropped is True
    assert result.legal_hold == {
        "legal_hold_outcome": "LIVRE",
        "legal_hold_motivo": lh.MOTIVO_SEM_HOLD_ATIVO,
        "legal_hold_ids": [],
        "legal_hold_registro_sha256": _SHA,
    }
    assert result.plan_evidence_ref == "EL-FIXTURE-R013"
    by_table = {o.tabela: o for o in result.layers}
    assert len(by_table) == len(ep.PERSISTENCE_LAYERS)
    assert by_table["audit_chain"].disposicao == "retido"
    assert by_table["agent_memory.embedding"].disposicao == "retirada"
    assert by_table["agent_memory"].rows_affected == 3 and by_table["agent_memory"].residual == 0


def test_relacao_retirada_mandada_eliminar_nao_exige_executor() -> None:
    journal: list[str] = []
    plan = _plan({"agent_checkpoints": "ELIMINAR", "agent_memory": "ELIMINAR"})
    executors = {"agent_memory": _Executor("agent_memory", journal)}
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )
    assert result.status == er.STATUS_COMPLETED


def test_falha_de_executor_para_a_cascata_e_nao_derruba_o_surrogate() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal, error=RuntimeError(f"boom {_PATIENT}"))
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )
    assert result.status == er.STATUS_FAILED
    assert "apply:erasure_log" not in journal and "drop:surrogate" not in journal
    assert result.errors == ["agent_memory: RuntimeError"]
    assert _PATIENT not in " ".join(result.errors)
    by_table = {o.tabela: o for o in result.layers}
    assert by_table["agent_memory"].disposicao == "falhou"
    assert by_table["erasure_log"].disposicao == "nao_executado"
    assert result.surrogate_dropped is False


def test_residuo_na_verificacao_nunca_e_completed() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal, residual=2)
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )
    assert result.status == er.STATUS_PARTIAL
    assert "agent_memory: residual" in result.errors
    assert result.episodic_erased is False


@pytest.mark.parametrize("bad", [True, -1, "3", None, 2.0])
def test_retorno_invalido_de_executor_e_falha(bad: Any) -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal, rows=bad)
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )
    assert result.status == er.STATUS_FAILED
    assert result.errors == ["agent_memory: invalid_executor_result"]


def test_residuo_no_surrogate_e_partial() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    result = _manager(
        journal, plan=plan, executors=executors, surrogate=_Surrogate(journal, residual=1)
    ).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert result.status == er.STATUS_PARTIAL
    assert result.surrogate_dropped is False


def test_verify_limpo_e_sujo() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    clean = _manager(journal, plan=plan, executors=executors).verify(_TENANT, _PATIENT)
    assert clean["status"] == er.STATUS_CLEAN and clean["episodic_clean"] is True
    assert not any(entry.startswith(("apply", "drop")) for entry in journal), "verify e somente leitura"

    plan2, executors2 = _eliminar_episodica(journal, residual=5)
    dirty = _manager(journal, plan=plan2, executors=executors2).verify(_TENANT, _PATIENT)
    assert dirty["status"] == er.STATUS_DIRTY and dirty["residual"] == ["agent_memory"]


class _RaisingRemainingExecutor(_Executor):
    def remaining(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int:
        self.journal.append(f"remaining:{self.name}")
        raise RuntimeError(f"boom {fhir_patient_id}")


class _RaisingRemainingSurrogate(_Surrogate):
    def remaining(self, tenant_id: str, fhir_patient_id: str) -> int:
        self.journal.append("remaining:surrogate")
        raise RuntimeError(f"boom {fhir_patient_id}")


class _InvalidDropSurrogate(_Surrogate):
    def drop(self, tenant_id: str, fhir_patient_id: str) -> int:
        self.journal.append("drop:surrogate")
        return None  # type: ignore[return-value]


def test_verificacao_de_executor_que_levanta_nunca_e_completed() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    executors["agent_memory"] = _RaisingRemainingExecutor("agent_memory", journal)
    result = _manager(journal, plan=plan, executors=executors).erase(
        _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
    )
    assert result.status == er.STATUS_PARTIAL
    assert "agent_memory: verify_failed" in result.errors
    assert {o.tabela: o for o in result.layers}["agent_memory"].residual is None
    assert _PATIENT not in " ".join(result.errors)


def test_verificacao_do_surrogate_que_levanta_nunca_e_completed() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    result = _manager(
        journal, plan=plan, executors=executors, surrogate=_RaisingRemainingSurrogate(journal)
    ).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert result.status == er.STATUS_PARTIAL
    assert result.surrogate_dropped is False
    assert "surrogate_mpi_id: verify_failed" in result.errors


def test_drop_do_surrogate_com_retorno_invalido_e_falha() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    result = _manager(
        journal, plan=plan, executors=executors, surrogate=_InvalidDropSurrogate(journal)
    ).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
    assert result.status == er.STATUS_PARTIAL
    assert result.surrogate_dropped is False
    assert "surrogate_mpi_id: invalid_store_result" in result.errors


@pytest.mark.parametrize("surrogate_kind", ["residual", "raises"])
def test_verify_com_surrogate_sujo_ou_ilegivel_e_dirty(surrogate_kind: str) -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal)
    surrogate = (
        _Surrogate(journal, residual=1)
        if surrogate_kind == "residual"
        else _RaisingRemainingSurrogate(journal)
    )
    report = _manager(journal, plan=plan, executors=executors, surrogate=surrogate).verify(_TENANT, _PATIENT)
    assert report["status"] == er.STATUS_DIRTY
    assert report["surrogate_clean"] is False


# ---------------------------------------------------------------------------
# PHI (ADR-0006) — nenhum valor de titular em log/erro
# ---------------------------------------------------------------------------


def test_nenhum_valor_de_titular_em_log_ou_recusa() -> None:
    journal: list[str] = []
    plan, executors = _eliminar_episodica(journal, error=ValueError(_PATIENT))
    with capture_logs() as captured:
        _manager(journal, plan=plan, executors=executors).erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
        for evaluator, kwargs in ((_retido, {}), (_livre, {"source": _Source(patient="x")})):
            try:
                _manager(journal, plan=plan, executors=executors, evaluator=evaluator, **kwargs).erase(
                    _TENANT, _PATIENT, titular_pseudo_id=_PSEUDO
                )
            except er.ErasureRefusedError as exc:
                assert _PATIENT not in str(exc) and _PSEUDO not in str(exc)
        with pytest.raises(er.ErasureCascadeDisabledError) as off:
            er.ErasureManager().erase(_TENANT, _PATIENT, titular_pseudo_id=_PSEUDO)
        assert _PATIENT not in str(off.value)
    assert captured
    for event in captured:
        rendered = str(event)
        assert _PATIENT not in rendered, event
        assert _PSEUDO not in rendered, event


# ---------------------------------------------------------------------------
# Dark por estrutura
# ---------------------------------------------------------------------------


def test_erasure_manager_sem_chamador_de_producao() -> None:
    """Nenhum modulo de `src/maezo` (fora o proprio) referencia `ErasureManager` em codigo.

    O reexport de `maezo.platform` e um `ImportFrom` + string de `__all__`, nao um chamador.
    A ROLL que ligar a cascata assume criar a amarracao, com revisor proprio.
    """
    offenders: list[str] = []
    for source in sorted(_SRC_MAEZO.rglob("*.py")):
        if source == _SRC_MAEZO / "platform" / "erasure.py":
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            is_name = isinstance(node, ast.Name) and node.id == "ErasureManager"
            is_attr = isinstance(node, ast.Attribute) and node.attr == "ErasureManager"
            if is_name or is_attr:
                offenders.append(str(source.relative_to(_REPO_ROOT)))
    assert offenders == [], f"a cascata ganhou chamador sem o pacote de habilitacao (ROLL): {offenders}"


def test_flag_da_cascata_ausente_de_deploy() -> None:
    """Nenhum `deploy/**` pode mencionar a flag (mesma sentenca das flags dark do programa)."""
    deploy = _REPO_ROOT / "deploy"
    files = [p for p in deploy.rglob("*") if p.is_file()]
    assert files, "deploy/** vazio: varrer nada nao e prova"
    hits = [str(p.relative_to(_REPO_ROOT)) for p in files if er.CASCADE_FLAG_ENV.encode() in p.read_bytes()]
    assert hits == []


def test_flag_default_off_lida_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    assert er.cascade_flag_enabled() is False
    monkeypatch.setenv(er.CASCADE_FLAG_ENV, " TRUE ")
    assert er.cascade_flag_enabled() is True
