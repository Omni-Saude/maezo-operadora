"""W5 CLNT 5.4: as DUAS pontes de identidade do erasure — substrato DARK, provas negativas.

O que estes testes travam, uma linha cada:

* as duas pontes sao EXATAMENTE as nomeadas pela Chave B do plano de eliminacao
  (`titular_pseudo_id->fhir_patient_id`, `thread_id->fhir_patient_id`) — conjunto fechado;
* cada ponte tem flag PROPRIA, OFF por padrao: flag OFF = recusa ANTES de qualquer efeito,
  e a fonte nunca e sequer tocada (o status quo de antes do modulo existir);
* grafias ambiguas da flag (`1`, `yes`, `on`, vazio) NUNCA habilitam — typo nao
  meia-habilita ponte de LGPD;
* flag ON sem decisao DPO registrada = fail-closed com o motivo preciso do carregador — e a
  fonte segue intocada (nenhuma travessia sem decisao);
* decisao divergente (relacao nao coberta; tenant divergente; fontes independentes
  discordando) e recusa tipada propria;
* ausencia de mapeamento e forma invalida sao recusa, nunca sucesso vazio;
* o erro/log nunca carrega a referencia pedida nem a identidade resolvida;
* o cruzamento so acontece com flag ON + plano ratificado + fonte vinculada;
* `resolve_titular_pseudo_id`/`resolve_thread_id` tem ZERO chamadores sob `src/maezo` — o
  substrato e dark por estrutura, nao por convencao (molde
  `test_retention_query_has_no_production_caller`).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from maezo.platform import identity_bridges as ib
from maezo.platform.lifecycle import erasure_plan as ep

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED_PLAN = _REPO_ROOT / ep.PLAN_RELPATH
_SRC_MAEZO = _REPO_ROOT / "src" / "maezo"

# Referencia e identidade-sentinela: se QUALQUER mensagem de erro/log carregar um destes
# valores, o teste de higiene PHI falha.
_PSEUDO_SENTINEL = "pseudo-sentinel-must-not-leak-0001"
_PATIENT_SENTINEL = "pat-sentinel-must-not-leak-0001"
_TENANT = "tenant-fix"


def _shipped_raw() -> dict[str, Any]:
    return dict(yaml.safe_load(_SHIPPED_PLAN.read_text(encoding="utf-8")))


def _ratified_yaml(**overrides: Any) -> dict[str, Any]:
    """O DRAFT enviado com todo placeholder preenchido — o ato de ratificar, em dados.

    Os valores sao de FIXTURE, rotulados como tal: nenhuma decisao de DPO existe neste PR.
    """
    data = _shipped_raw()
    data.update(
        {
            "status": "RATIFICADO",
            "ratificado": True,
            "dpo_review": "APPROVED",
            "dpo_reviewer": "DPO fixture reviewer",
            "dpo_review_date": "2026-01-01",
            "evidence_ref": "EL-FIXTURE-0001",
            "notes": "fixture ratification, unit test only",
            "review_packet": "docs/reviews/adr-0029-erasure-packet.md",
        }
    )
    for layer in data["camadas"]:
        layer["decisao_dpo"] = "RETER_COM_BASE_LEGAL"
        layer["base_legal"] = "LGPD art. 16 I (fixture value, not a legal opinion)"
        layer["retencao"] = "5 anos (fixture value)"
    data.update(overrides)
    return data


def _write_plan(tmp_path: Path, data: Mapping[str, Any], name: str = "plan.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(dict(data), allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def ratified_plan(tmp_path: Path) -> ep.ErasurePlan:
    """Um plano RATIFICADO de fixture — a decisao DPO registrada, em dados de teste."""
    return ep.load_erasure_plan(_write_plan(tmp_path, _ratified_yaml()))


@pytest.fixture(autouse=True)
def _flags_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Toda a suite comeca com as duas flags OFF e sem override de plano — o default."""
    monkeypatch.delenv(ib.TITULAR_BRIDGE_FLAG_ENV, raising=False)
    monkeypatch.delenv(ib.THREAD_BRIDGE_FLAG_ENV, raising=False)
    monkeypatch.delenv(ep.PLAN_PATH_ENV, raising=False)


class _RecordingSource:
    """Fonte de teste que registra chamadas e devolve o que lhe foi ensaiado."""

    def __init__(self, resolution: Any = "ok", error: Exception | None = None) -> None:
        self._resolution = resolution
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def resolve(self, tenant_id: str, reference: str) -> Any:
        """`Any` de proposito: a suite ensaia TAMBE formas invalidas de resolucao."""
        self.calls.append((tenant_id, reference))
        if self._error is not None:
            raise self._error
        if self._resolution == "ok":
            return ib.BridgeResolution(tenant_id=tenant_id, fhir_patient_id=_PATIENT_SENTINEL)
        return self._resolution


def _source(**kwargs: Any) -> _RecordingSource:
    return _RecordingSource(**kwargs)


# ---------------------------------------------------------------------------
# As duas pontes: identidade, fechamento e flags proprias
# ---------------------------------------------------------------------------


def test_as_duas_pontes_sao_as_nomeadas_pela_chave_b_do_plano() -> None:
    """O conjunto de pontes e EXATAMENTE o da Chave B do erasure-plan.template.yaml."""
    assert ib.KNOWN_BRIDGE_KINDS == (
        "titular_pseudo_id->fhir_patient_id",
        "thread_id->fhir_patient_id",
    )
    # A grafia EXATA do artefato normativo (nomes entre crases, "->" sem espacos).
    shipped = _SHIPPED_PLAN.read_text(encoding="utf-8")
    assert "`titular_pseudo_id`->`fhir_patient_id`" in shipped
    assert "`thread_id`->`fhir_patient_id`" in shipped


def test_cada_ponte_tem_flag_propria_e_conjunto_fechado() -> None:
    """Duas instancias, flags DISTINTAS, e nenhuma terceira ponte existe no modulo."""
    assert ib.TITULAR_IDENTITY_BRIDGE.flag_env == "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED"
    assert ib.THREAD_IDENTITY_BRIDGE.flag_env == "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED"
    assert ib.TITULAR_IDENTITY_BRIDGE.flag_env != ib.THREAD_IDENTITY_BRIDGE.flag_env
    kinds = {ib.TITULAR_IDENTITY_BRIDGE.kind, ib.THREAD_IDENTITY_BRIDGE.kind}
    assert len(kinds) == 2
    assert set(ib.KNOWN_BRIDGE_KINDS) == {k.value for k in kinds}


def test_as_relacoes_que_cada_ponte_desbloqueia_sao_as_do_encadeamento() -> None:
    """Ponte A = as relacoes com coluna fhir_patient_id; Ponte B = a camada de thread.

    Derivado da PROPIA enumeracao do carregador, para a ponte nunca divergir em silencio do
    que o dry-run reporta.
    """
    fhir_keyed = {
        layer.tabela for layer in ep.PERSISTENCE_LAYERS if layer.subject_column == "fhir_patient_id"
    }
    assert set(ib.TITULAR_IDENTITY_BRIDGE.source_relations) == fhir_keyed
    thread_keyed = {
        layer.tabela
        for layer in ep.PERSISTENCE_LAYERS
        if layer.camada == "trabalho"
        and "thread_id" in layer.identificacao
        and layer.resolucao is ep.IdentityResolution.NAO_PROVISIONADA
    }
    assert set(ib.THREAD_IDENTITY_BRIDGE.source_relations) == thread_keyed


# ---------------------------------------------------------------------------
# Flag OFF = status quo: recusa antes de qualquer efeito, fonte intocada
# ---------------------------------------------------------------------------


def test_flags_padrao_off_sem_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ib.bridge_flag_enabled(ib.TITULAR_BRIDGE_FLAG_ENV) is False
    assert ib.bridge_flag_enabled(ib.THREAD_BRIDGE_FLAG_ENV) is False
    monkeypatch.setenv(ib.TITULAR_BRIDGE_FLAG_ENV, "")
    monkeypatch.setenv(ib.THREAD_BRIDGE_FLAG_ENV, "   ")
    assert ib.bridge_flag_enabled(ib.TITULAR_BRIDGE_FLAG_ENV) is False
    assert ib.bridge_flag_enabled(ib.THREAD_BRIDGE_FLAG_ENV) is False


@pytest.mark.parametrize(
    "grafia",
    ["1", "yes", "on", "verdadeiro", "enabled", "false", "0", "tru"],
)
def test_grafias_ambiguas_da_flag_nunca_habilitam(monkeypatch: pytest.MonkeyPatch, grafia: str) -> None:
    """So o literal `true` (case-insensitive) habilita; typo nao meia-habilita ponte."""
    monkeypatch.setenv(ib.TITULAR_BRIDGE_FLAG_ENV, grafia)
    assert ib.bridge_flag_enabled(ib.TITULAR_BRIDGE_FLAG_ENV) is False


def test_ponte_a_flag_off_recusa_e_fonte_fica_intocada(ratified_plan: ep.ErasurePlan) -> None:
    """OFF = o status quo: recusa tipada ANTES de qualquer efeito; a fonte nao e chamada."""
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT, titular_pseudo_id=_PSEUDO_SENTINEL, source=source, plan=ratified_plan
        )
    assert excinfo.value.reason == ib.REASON_FLAG_OFF
    assert source.calls == []


def test_ponte_b_flag_off_recusa_e_fonte_fica_intocada(ratified_plan: ep.ErasurePlan) -> None:
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_thread_id(tenant_id=_TENANT, thread_id="thread-fix-1", source=source, plan=ratified_plan)
    assert excinfo.value.reason == ib.REASON_FLAG_OFF
    assert source.calls == []


def test_flag_off_recusa_ate_com_fonte_e_plano_e_confirmacao_prontos(
    ratified_plan: ep.ErasurePlan,
) -> None:
    """Nenhuma combinacao de insumos contorna a flag: ela e o primeiro gate."""
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            confirm_with=_source(),
        )
    assert excinfo.value.reason == ib.REASON_FLAG_OFF
    assert source.calls == []


def test_env_true_habilita_a_flag_da_sua_ponte_so(
    monkeypatch: pytest.MonkeyPatch, ratified_plan: ep.ErasurePlan
) -> None:
    """Cada flag habilita a SUA ponte — e somente ela."""
    monkeypatch.setenv(ib.TITULAR_BRIDGE_FLAG_ENV, "true")
    source_a = _source()
    assert (
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source_a,
            plan=ratified_plan,
        )
        == _PATIENT_SENTINEL
    )
    source_b = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_thread_id(tenant_id=_TENANT, thread_id="thread-fix-1", source=source_b, plan=ratified_plan)
    assert excinfo.value.reason == ib.REASON_FLAG_OFF
    assert source_b.calls == []


def test_flag_on_explicita_por_parametro_e_honrada(ratified_plan: ep.ErasurePlan) -> None:
    """`enabled=True` explicito vale como flag (a ROLL amarra o env no call site)."""
    source = _source()
    assert (
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
        == _PATIENT_SENTINEL
    )
    assert source.calls == [(_TENANT, _PSEUDO_SENTINEL)]


# ---------------------------------------------------------------------------
# Flag ON sem decisao DPO registrada = fail-closed; fonte intocada
# ---------------------------------------------------------------------------


def test_on_sem_decisao_dpo_registrada_recusa_com_motivo_do_carregador() -> None:
    """Sem plano ratificado, a recusa vem do carregador (`not_ratified`) — e a fonte NUNCA
    e tocada: nenhuma travessia acontece antes da decisao."""
    source = _source()
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT, titular_pseudo_id=_PSEUDO_SENTINEL, source=source, enabled=True
        )
    assert excinfo.value.reason == ep.REASON_NOT_RATIFIED
    assert source.calls == []


def test_on_sem_decisao_dpo_registrada_ponte_b_tambem_recusa() -> None:
    source = _source()
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ib.resolve_thread_id(tenant_id=_TENANT, thread_id="thread-fix-1", source=source, enabled=True)
    assert excinfo.value.reason == ep.REASON_NOT_RATIFIED
    assert source.calls == []


def test_on_com_decisao_de_vocabulario_invalido_recusa_tipada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`decisao_dpo` fora do vocabulario fechado -> motivo `unknown_decision` do carregador."""
    path = _write_plan(tmp_path, _ratified_yaml())
    data = _ratified_yaml()
    for layer in data["camadas"]:
        layer["decisao_dpo"] = "APAGAR_TUDO"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv(ep.PLAN_PATH_ENV, str(path))
    source = _source()
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT, titular_pseudo_id=_PSEUDO_SENTINEL, source=source, enabled=True
        )
    assert excinfo.value.reason == ep.REASON_UNKNOWN_DECISION
    assert source.calls == []


def test_on_com_plano_que_nao_cobre_as_relacoes_da_ponte_recusa_divergencia() -> None:
    """Defesa local da ponte: plano registrado sem `erasure_log` = divergencia de cobertura.

    (O carregador ja recusa o conjunto incompleto; este gate cobre a ponte contra um
    `ErasurePlan` construido/transportado fora do carregador.)
    """
    decisions = {
        tabela: ep.ErasureLayerDecision(
            camada="episodica",
            tabela=tabela,
            decisao_dpo="ELIMINAR",
            base_legal="fixture",
            retencao="fixture",
        )
        for tabela in ("agent_memory",)
    }
    partial_plan = ep.ErasurePlan(
        dpo_reviewer="fixture",
        dpo_review_date="2026-01-01",
        evidence_ref="EL-FIXTURE",
        review_packet="fixture",
        notes="fixture",
        decisions=MappingProxyType(decisions),
    )
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=partial_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_RELATION_COVERAGE_DIVERGENCE
    assert source.calls == []


# ---------------------------------------------------------------------------
# Insumos e amarracao: ausencia e recusa tipada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tenant", ["", "   ", None])
def test_tenant_em_branco_recusa(ratified_plan: ep.ErasurePlan, tenant: str | None) -> None:
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=tenant,  # type: ignore[arg-type]
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_TENANT_BLANK
    assert source.calls == []


@pytest.mark.parametrize("ref", ["", "   ", None])
def test_referencia_em_branco_recusa(ratified_plan: ep.ErasurePlan, ref: str | None) -> None:
    source = _source()
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=ref,  # type: ignore[arg-type]
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_REFERENCE_BLANK
    assert source.calls == []


def test_fonte_nao_vinculada_recusa_sem_inventar_mapeamento(
    ratified_plan: ep.ErasurePlan,
) -> None:
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=None,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_SOURCE_UNBOUND


# ---------------------------------------------------------------------------
# Divergencias e formas invalidas na travessia
# ---------------------------------------------------------------------------


def test_fonte_que_levanta_recusa_com_o_tipo_apenas(ratified_plan: ep.ErasurePlan) -> None:
    """A mensagem da excecao (que pode ecoar bind values) NUNCA entra na recusa."""
    boom = RuntimeError(f"conn failed while binding {_PSEUDO_SENTINEL} {_PATIENT_SENTINEL}")
    source = _source(error=boom)
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_SOURCE_FAILED
    assert "RuntimeError" in excinfo.value.detail
    assert _PSEUDO_SENTINEL not in str(excinfo.value)
    assert _PATIENT_SENTINEL not in str(excinfo.value)


def test_mapeamento_ausente_e_recusa_nunca_sucesso_vazio(ratified_plan: ep.ErasurePlan) -> None:
    source = _source(resolution=None)
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_NO_MAPPING


@pytest.mark.parametrize("shape_errado", ["pat-como-string", 42, ["pat"], {"id": "pat"}])
def test_forma_invalida_de_resolucao_recusa(ratified_plan: ep.ErasurePlan, shape_errado: object) -> None:
    source = _source(resolution=shape_errado)
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_INVALID_RESOLUTION


def test_resolucao_com_identidade_em_branco_recusa(ratified_plan: ep.ErasurePlan) -> None:
    source = _source(resolution=ib.BridgeResolution(tenant_id=_TENANT, fhir_patient_id="   "))
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_INVALID_RESOLUTION


def test_divergencia_de_tenant_recusa(ratified_plan: ep.ErasurePlan) -> None:
    source = _source(
        resolution=ib.BridgeResolution(tenant_id="outro-tenant", fhir_patient_id=_PATIENT_SENTINEL)
    )
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=source,
            plan=ratified_plan,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_TENANT_DIVERGENCE


def test_divergencia_entre_fontes_independentes_recusa(ratified_plan: ep.ErasurePlan) -> None:
    """Com `confirm_with` provisionado, discordancia = recusa tipada, nunca escolha."""
    primary = _source()
    conflicting = _source(resolution=ib.BridgeResolution(tenant_id=_TENANT, fhir_patient_id="pat-outro-0002"))
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=primary,
            plan=ratified_plan,
            confirm_with=conflicting,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_RESOLUTION_DIVERGENCE


def test_confirmacao_por_segunda_fonte_de_tenant_divergente_recusa(
    ratified_plan: ep.ErasurePlan,
) -> None:
    primary = _source()
    conflicting = _source(
        resolution=ib.BridgeResolution(tenant_id="outro-tenant", fhir_patient_id=_PATIENT_SENTINEL)
    )
    with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=primary,
            plan=ratified_plan,
            confirm_with=conflicting,
            enabled=True,
        )
    assert excinfo.value.reason == ib.REASON_TENANT_DIVERGENCE


def test_confirmacao_concordante_avanca_a_travessia(ratified_plan: ep.ErasurePlan) -> None:
    """Cruzamento so com TUDO valido — e ambas as fontes recebem exatamente o escopo pedido."""
    primary = _source()
    confirming = _source()
    assert (
        ib.resolve_thread_id(
            tenant_id=_TENANT,
            thread_id="thread-fix-1",
            source=primary,
            plan=ratified_plan,
            confirm_with=confirming,
            enabled=True,
        )
        == _PATIENT_SENTINEL
    )
    assert primary.calls == [(_TENANT, "thread-fix-1")]
    assert confirming.calls == [(_TENANT, "thread-fix-1")]


# ---------------------------------------------------------------------------
# Higiene PHI: nenhuma referencia/identidade em erro; nenhum valor em log
# ---------------------------------------------------------------------------


def test_nenhuma_recusa_carrega_a_referencia_ou_a_identidade(
    ratified_plan: ep.ErasurePlan,
) -> None:
    """Toda a taxonomia de recusa, com sentinela plantado em TODA entrada/saida."""
    cases: list[tuple[_RecordingSource, _RecordingSource | None]] = [
        (_source(resolution=None), None),  # no_mapping
        (_source(resolution="forma-errada"), None),  # invalid_resolution
        (_source(error=RuntimeError("boom")), None),  # source_failed
        (
            _source(resolution=ib.BridgeResolution(tenant_id="outro", fhir_patient_id="pat-outro")),
            None,
        ),  # tenant_divergence
        (
            _source(),
            _source(resolution=ib.BridgeResolution(tenant_id=_TENANT, fhir_patient_id="pat-outro")),
        ),  # resolution_divergence via confirm
    ]
    for source, confirm in cases:
        with pytest.raises(ib.ErasureBridgeRefusedError) as excinfo:
            ib.resolve_titular_pseudo_id(
                tenant_id=_TENANT,
                titular_pseudo_id=_PSEUDO_SENTINEL,
                source=source,
                plan=ratified_plan,
                confirm_with=confirm,
                enabled=True,
            )
        message = str(excinfo.value)
        assert _PSEUDO_SENTINEL not in message, excinfo.value.reason
        assert _PATIENT_SENTINEL not in message, excinfo.value.reason


def test_nenhum_valor_de_identidade_no_log_de_recusa(ratified_plan: ep.ErasurePlan) -> None:
    """Os eventos de log da recusa carregam ponte/motivo/tenant — nunca valores de titular."""
    with capture_logs() as captured, pytest.raises(ib.ErasureBridgeRefusedError):
        ib.resolve_titular_pseudo_id(
            tenant_id=_TENANT,
            titular_pseudo_id=_PSEUDO_SENTINEL,
            source=_source(resolution=None),
            plan=ratified_plan,
            enabled=True,
        )
    assert captured, "a recusa deve logar o motivo (sem valores)"
    for event in captured:
        rendered = str(event)
        assert _PSEUDO_SENTINEL not in rendered
        assert _PATIENT_SENTINEL not in rendered
    assert any(event.get("reason") == ib.REASON_NO_MAPPING for event in captured)


# ---------------------------------------------------------------------------
# Dark por estrutura: zero chamadores sob src/maezo; mecanismo intacto
# ---------------------------------------------------------------------------


def test_as_resolucoes_nao_tem_chamador_de_producao() -> None:
    """`resolve_titular_pseudo_id`/`resolve_thread_id` nao tem NENHUMA referencia de codigo
    (Name/Attribute) sob `src/maezo` — a definicao (`def`) nao e um chamador, no mesmo
    idioma do guard de `retention_query`. Se um PR futuro amarrar a ponte sem o pacote de
    habilitacao (ROLL), este teste falha."""
    offenders: list[str] = []
    names = {"resolve_titular_pseudo_id", "resolve_thread_id"}
    for source in sorted(_SRC_MAEZO.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            is_name = isinstance(node, ast.Name) and node.id in names
            is_attr = isinstance(node, ast.Attribute) and node.attr in names
            if is_name or is_attr:
                offenders.append(str(source.relative_to(_REPO_ROOT)))
    assert offenders == [], f"a ponte de identidade ganhou chamador sem o pacote de habilitacao: {offenders}"


def test_erasure_manager_segue_recusando_o_mecanismo_inteiro() -> None:
    """A ponte NAO abre o mecanismo: erase/verify seguem sempre-raising (T3.4-F3)."""
    from maezo.platform.erasure import ErasureManager, ErasureNotImplementedError

    manager = ErasureManager()
    with pytest.raises(ErasureNotImplementedError):
        manager.erase(_TENANT, _PATIENT_SENTINEL)
    with pytest.raises(ErasureNotImplementedError):
        manager.verify(_TENANT, _PATIENT_SENTINEL)
