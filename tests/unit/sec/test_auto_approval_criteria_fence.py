"""Structural fence for the SP-OP-AUTH-001 auto-approval criteria gate (GAP-AUTH-4).

These are SPEC-level and ARCHITECTURE-level assertions, not worker-behaviour pins: they read the
real `spec/**` artifacts and the real modules, and they are the tests that genuinely FAILED
before this change. They encode the four structural claims GAP-AUTH-4 was about:

  1. no token can reach `BRT_AutoApproval` without passing through the deterministic validator;
  2. the DMN's favourable rule cannot fire without PROOF the validator ran
     (`auto_criteria_verificado`) — the execution fence;
  3. the DMN never has a denial output, on any rule (L0 hard, ADR-0005/0008);
  4. the refusal evidence reaches the durable ADR-0007 audit chain, not just engine history.

Sibling of `test_dentro_teto_source.py` (the T1.9 ceiling-origination architecture test) — same
posture: prove the property against the REAL artifacts, never a fixture copy of them.
"""

from __future__ import annotations

import ast
import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.tools.workers import auth
from maezo.tools.workers.auth_criteria import load_criteria_sources
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import (
    _ENUM_TOKEN_RE,
    _SAFE_DECISION_BASIS_KEYS,
    _is_bounded_token,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = _REPO_ROOT / "spec" / "processes"
_AUTH_BPMN = _SPEC / "bpmn" / "SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_AUTO_APPROVAL_DMN = _SPEC / "dmn" / "auth_auto_approval.dmn"
_CONTRATUAL_DMN = _SPEC / "dmn" / "auth_criteria_contratual.dmn"
_RATIFICATION = _SPEC / "dmn" / "auth-criteria-ratification.yaml"
_TENANTS_AMH = _REPO_ROOT / "spec" / "policies" / "autonomy" / "tenants-amh.yaml"

_BPMN_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
_VALIDATOR_TASK = "ST_ValidateAutoApprovalCriteria"
_VALIDATOR_TOPIC = "operadora.auth.validate_auto_criteria"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


@pytest.fixture(scope="module")
def bpmn_process() -> ET.Element:
    process = ET.parse(_AUTH_BPMN).getroot().find("bpmn:process", _BPMN_NS)
    assert process is not None
    return process


@pytest.fixture(scope="module")
def auto_approval_table() -> ET.Element:
    root = ET.parse(_AUTO_APPROVAL_DMN).getroot()
    table = next(el for el in root.iter() if _local(el.tag) == "decisionTable")
    return table


def _rules(table: ET.Element) -> list[ET.Element]:
    return [el for el in table if _local(el.tag) == "rule"]


def _entry_texts(rule: ET.Element, kind: str) -> list[str]:
    out: list[str] = []
    for el in rule:
        if _local(el.tag) != kind:
            continue
        text = next((c.text for c in el if _local(c.tag) == "text"), None)
        out.append((text or "").strip())
    return out


# ---------------------------------------------------------------------------------------------
# 1. The token cannot reach BRT_AutoApproval without the validator
# ---------------------------------------------------------------------------------------------


def test_validator_task_is_the_sole_predecessor_of_brt_auto_approval(bpmn_process: ET.Element) -> None:
    """GAP-AUTH-4's structural core: nothing reaches the auto-approval DMN unvalidated.

    Before this change the ONLY inbound flow was `BRT_SlaAnalise -> BRT_AutoApproval`, so the
    table decided on start-payload seeds. This assertion is what makes "a worker computes the
    facts first" a property of the MODEL rather than a claim in a docstring.
    """
    flows = [el for el in bpmn_process if _local(el.tag) == "sequenceFlow"]
    into_brt = [f for f in flows if f.get("targetRef") == "BRT_AutoApproval"]
    assert into_brt, "BRT_AutoApproval has no inbound sequence flow at all"
    sources = {f.get("sourceRef") or "" for f in into_brt}
    assert sources == {_VALIDATOR_TASK}, (
        f"BRT_AutoApproval must be reachable ONLY from {_VALIDATOR_TASK}; got {sorted(sources)}"
    )


def test_validator_task_is_an_external_task_on_the_deterministic_topic(bpmn_process: ET.Element) -> None:
    """The validator is a DETERMINISTIC external-task worker, never a DMN and never an agent."""
    task = next((el for el in bpmn_process if el.get("id") == _VALIDATOR_TASK), None)
    assert task is not None, f"{_VALIDATOR_TASK} is not declared in SP-OP-AUTH-001"
    assert _local(task.tag) == "serviceTask"
    camunda = "{http://camunda.org/schema/1.0/bpmn}"
    assert task.get(f"{camunda}type") == "external"
    assert task.get(f"{camunda}topic") == _VALIDATOR_TOPIC


def test_validator_task_is_fed_by_brt_sla_analise(bpmn_process: ET.Element) -> None:
    """Position pinned: it sits BETWEEN BRT_SlaAnalise and BRT_AutoApproval (design §4)."""
    flows = [el for el in bpmn_process if _local(el.tag) == "sequenceFlow"]
    sources = {f.get("sourceRef") or "" for f in flows if f.get("targetRef") == _VALIDATOR_TASK}
    assert sources == {"BRT_SlaAnalise"}, sorted(sources)


def test_validator_task_declares_no_error_boundary_event(bpmn_process: ET.Element) -> None:
    """ADR-0030: an unmodeled `bpmnError` silently ENDS the process scope on CIB Seven 2.1.0.

    The validator has no boundary, so it must never raise — pinned in source by
    `test_validator_worker_never_raises_a_bpmn_error` below. This asserts the other half: that
    nobody later attaches a boundary here and quietly changes the contract.
    """
    boundaries = [
        el
        for el in bpmn_process
        if _local(el.tag) == "boundaryEvent" and el.get("attachedToRef") == _VALIDATOR_TASK
    ]
    assert boundaries == [], (
        f"{_VALIDATOR_TASK} must declare NO boundary event; found {[b.get('id') for b in boundaries]}"
    )


def test_validator_worker_never_raises_a_bpmn_error() -> None:
    """AST proof (ADR-0030): `ValidateAutoCriteriaWorker` contains no `raise WorkerBpmnError`.

    The task has no modeled boundary to a NEUTRAL terminal, so a raise there would end the scope
    silently — the live-verified hazard. Every refusal is a RETURNED record instead.
    """
    tree = ast.parse(Path(auth.__file__).read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ValidateAutoCriteriaWorker"
    )
    raises = [
        n
        for n in ast.walk(cls)
        if isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "WorkerBpmnError"
    ]
    assert raises == [], "ValidateAutoCriteriaWorker must never raise WorkerBpmnError (ADR-0030)"


# ---------------------------------------------------------------------------------------------
# 2. The execution fence + 3. no denial output
# ---------------------------------------------------------------------------------------------

_EXPECTED_DMN_INPUTS = [
    "auto_criteria_verificado",
    "criterio_tecnico_ok",
    "criterio_financeiro_ok",
    "criterio_regulatorio_ok",
    "criterio_contratual_ok",
    "carater_atendimento",
]


def test_auto_approval_dmn_reads_only_computed_criteria(auto_approval_table: ET.Element) -> None:
    """The rewired inputs. The three unverified seeds (`dut_atendida`/`dentro_teto_l2`/
    `rede_credenciada`) are GONE from this table — that is the defect, expressed as an assertion.
    `carater_atendimento` is deliberately RETAINED so an SME can differentiate urgency later
    without a code change (ADR-0012 keeps combination policy in the DMN)."""
    inputs = [
        (next(c.text for c in el.iter() if _local(c.tag) == "text") or "").strip()
        for el in auto_approval_table
        if _local(el.tag) == "input"
    ]
    assert inputs == _EXPECTED_DMN_INPUTS, inputs
    for seeded in ("dut_atendida", "dentro_teto_l2", "rede_credenciada"):
        assert seeded not in inputs


def test_only_rule_producing_auto_aprovar_requires_all_five_booleans(
    auto_approval_table: ET.Element,
) -> None:
    """THE FENCE. Every rule that can emit `AUTO_APROVAR` must demand `true` on all five
    computed booleans — including `auto_criteria_verificado`, the proof the validator RAN.
    Skip the validator and the token falls to the catch-all -> human review."""
    favourable = [
        r for r in _rules(auto_approval_table) if '"AUTO_APROVAR"' in _entry_texts(r, "outputEntry")
    ]
    assert len(favourable) == 1, "exactly one favourable rule is expected"
    inputs = _entry_texts(favourable[0], "inputEntry")
    assert inputs[:5] == ["true"] * 5, inputs
    # carater_atendimento is the reserved SME axis — don't-care today, on purpose.
    assert inputs[5] == "-"


def test_auto_approval_dmn_has_a_catch_all_resolving_to_human_review(
    auto_approval_table: ET.Element,
) -> None:
    """Fail-safe by omission: the LAST rule matches everything and routes to a human."""
    assert auto_approval_table.get("hitPolicy") == "FIRST"
    last = _rules(auto_approval_table)[-1]
    assert set(_entry_texts(last, "inputEntry")) == {"-"}
    assert '"ANALISE_HUMANA"' in _entry_texts(last, "outputEntry")


def test_auto_approval_dmn_can_never_emit_a_denial(auto_approval_table: ET.Element) -> None:
    """L0 hard (ADR-0005/0008): the ONLY recommendations this table can emit are AUTO_APROVAR
    and ANALISE_HUMANA. A denial is born exclusively in a human User Task."""
    verdicts = {_entry_texts(r, "outputEntry")[0] for r in _rules(auto_approval_table)}
    assert verdicts == {'"AUTO_APROVAR"', '"ANALISE_HUMANA"'}, verdicts


def test_contratual_dmn_is_an_honest_empty_seam() -> None:
    """The contractual table has exactly ONE rule — a catch-all returning false. No milestone,
    rule or KPI was invented; nothing here can grant anything."""
    table = next(el for el in ET.parse(_CONTRATUAL_DMN).getroot().iter() if _local(el.tag) == "decisionTable")
    rules = _rules(table)
    assert len(rules) == 1
    assert set(_entry_texts(rules[0], "inputEntry")) == {"-"}
    assert _entry_texts(rules[0], "outputEntry") == ["false", '"SEM_REGRA_RATIFICADA"']


# ---------------------------------------------------------------------------------------------
# 4. Evidence reaches the durable audit chain
# ---------------------------------------------------------------------------------------------

_AUDIT_KEYS = (
    "criterio_tecnico_ok",
    "criterio_financeiro_ok",
    "criterio_regulatorio_ok",
    "criterio_contratual_ok",
    "auto_criteria_verificado",
    "motivo_bloqueio_criterios",
)


def test_criteria_evidence_reaches_the_durable_audit_chain() -> None:
    """GK-ceiling finding 3, applied to the criteria gate: without these keys a criteria-refused
    request writes an ADR-0007 row reading `decision=COMPLETE` with NO evidence of why."""
    missing = [k for k in _AUDIT_KEYS if k not in _SAFE_DECISION_BASIS_KEYS]
    assert missing == [], missing


def test_every_failure_and_shadow_token_is_a_bounded_non_phi_token() -> None:
    """The whole vocabulary is a closed enum of bounded tokens — never free text, so nothing
    the gate emits can smuggle PHI or an identifier into the clear (harness §3.3)."""
    tokens = _all_criteria_tokens()
    assert tokens, "token vocabulary must not be empty"
    for token in tokens:
        assert _ENUM_TOKEN_RE.match(token), f"{token!r} is not a bounded audit token"
        assert _is_bounded_token(token)


def _all_criteria_tokens() -> list[str]:
    """Every `_FIN_*`/`_TEC_*`/`_REG_*`/`_CON_*`/degradation token plus the shadow combinations."""
    tokens = [
        value
        for name, value in vars(auth).items()
        if isinstance(value, str) and name.startswith(("_FIN_", "_TEC_", "_REG_", "_CON_", "_VALIDADOR_"))
    ]
    suffixes = [auth._SOMBRA_APROVARIA, auth._SOMBRA_REPROVARIA, auth._SOMBRA_INDETERMINADO]
    tokens += [f"{p}{s}" for p in ("TECNICO", "REGULATORIO", "CONTRATUAL") for s in suffixes]
    return tokens


# ---------------------------------------------------------------------------------------------
# Registration + the shipped ratification state
# ---------------------------------------------------------------------------------------------


def test_the_validator_topic_is_registered_by_the_auth_bootstrap() -> None:
    """A modeled task with no registered worker STALLS every authorization request."""
    from maezo.tools.workers.bootstrap import register_all_workers
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="criteria-fence-probe")
    # No `dmn=` seam is passed on purpose: an ABSENT seam must still register the worker, or the
    # modeled task would go unserved and STALL every authorization request. The missing seam
    # degrades each DMN-backed criterion to `*_TABELA_INDISPONIVEL` -> human review instead.
    register_all_workers(harness)
    assert _VALIDATOR_TOPIC in harness.registered_topics


def test_shipped_manifest_ratifies_nothing() -> None:
    """The honest state of the repo today, asserted so a silent ratification cannot slip in.

    If this test starts failing, someone RATIFIED a clinical/regulatory/contractual rule source.
    That is a legitimate act — but it must be a reviewed, deliberate one, so it has to update
    this pin (and `docs/review-queue.md`) alongside the manifest.
    """
    data: dict[str, Any] = yaml.safe_load(_RATIFICATION.read_text(encoding="utf-8"))
    ratified = [k for k, v in data["fontes"].items() if v.get("ratificado") is True]
    assert ratified == [], f"unexpectedly ratified rule sources: {ratified}"


def test_rede_credenciada_declarada_como_criterio_sem_cobertura() -> None:
    """GK-criteria finding 2/5b: `rede_credenciada` nao e coberto por nenhum criterio hoje.

    Antes deste portao ele era um dos tres booleanos SEMEADOS que a DMN lia; agora simplesmente
    nao participa. Isso e fail-closed HOJE (nada auto-aprova), mas quando `auth_criteria_contratual`
    for ratificado a aprovacao automatica poderia ser concedida a um prestador FORA DA REDE sem que
    criterio algum tivesse dito nada. Declarar em prosa nao basta — o manifesto e o arquivo que o
    SME OBRIGATORIAMENTE toca, entao a declaracao vive la e este teste impede que suma em silencio.

    ESTAS AFIRMACOES SAO SOBRE O TEXTO do manifesto, e por si so NAO provam nada em runtime — era
    exatamente esse o defeito M-3 (a declaracao existia e o loader nao a lia). A ultima afirmacao
    fecha a distancia: o mesmo arquivo, lido pelo loader real, tem de SUPRIMIR aquela fonte.
    """
    manifest = yaml.safe_load(_RATIFICATION.read_text(encoding="utf-8"))
    nao_cobertos = manifest.get("criterios_nao_cobertos") or {}
    assert "rede_credenciada" in nao_cobertos, (
        "rede_credenciada deixou de ser declarado como criterio sem cobertura — se uma fonte de "
        "credenciamento de rede passou a existir, remova a entrada E ligue o criterio; se nao, "
        "a entrada tem de continuar aqui."
    )
    entry = nao_cobertos["rede_credenciada"]
    assert entry.get("bloqueia_ratificacao_de") == "auth_criteria_contratual", (
        "a entrada precisa dizer QUAL ratificacao ela bloqueia, senao o SME nao a ve no momento certo"
    )
    assert str(entry.get("revisor_necessario", "")).strip(), "sem revisor nomeado a entrada e inerte"
    assert dict(load_criteria_sources(_RATIFICATION).blocked_by_uncovered) == {
        "auth_criteria_contratual": ("rede_credenciada",)
    }, "a declaracao acima tem de chegar ao runtime — senao volta a ser texto inerte (M-3)"


class _AlwaysWithinCeiling:
    """Stand-in for `CeilingResolver`: the financial criterion is not what is under test here."""

    def within_l2_ceiling(self, **_kwargs: Any) -> bool:
        return True


def _all_green_worker(manifest: Path) -> auth.ValidateAutoCriteriaWorker:
    """A validator whose every table returns its FAVOURABLE verdict, over `manifest`.

    So the ONLY thing that can keep a criterion false is the ratification gate itself.
    """
    dmn = FakeDmnTransport()
    dmn.register("dut_rol_coverage", [{"no_rol": False, "requer_dut": False, "dut_ref": "NAO_APLICA"}])
    dmn.register("carencia_check", [{"carencia_cumprida": True, "prazo_restante_dias": 0}])
    dmn.register("auth_criteria_contratual", [{"criterio_contratual_ok": True, "motivo": "OK"}])
    return auth.ValidateAutoCriteriaWorker(
        resolver=_AlwaysWithinCeiling(),  # type: ignore[arg-type]
        dmn=dmn,
        sources=load_criteria_sources(manifest),
    )


_GREEN_VARS: dict[str, Any] = {
    "tenant_id": "amh",
    "numero_guia_tiss": "GUIA-FENCE",
    "valor_estimado_brl": 180.0,
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "tipo_procedimento": "eletivo",
    "dias_desde_adesao": 400,
    "cpt_declarada": False,
}


def test_um_criterio_sem_cobertura_impede_o_pass_mesmo_com_a_fonte_ratificada(
    tmp_path: Path, auto_approval_table: ET.Element
) -> None:
    """RUNTIME ENFORCEMENT do defeito M-3, contra o manifesto REAL.

    Cenario: um SME faz exatamente a "mudanca de dados" que o desenho promete — flipa TODAS as
    fontes do arquivo real para `ratificado: true` com revisor e data — e esquece (ou nao ve) a
    entrada `criterios_nao_cobertos: rede_credenciada`. Antes desta correcao isso abria aprovacao
    automatica para um prestador FORA DA REDE sem que nenhum criterio tivesse dito nada. Agora
    `criterio_contratual_ok` sai FALSE, e a unica regra favoravel de `auth_auto_approval.dmn`
    exige `true` nele — logo a DMN NAO pode emitir `AUTO_APROVAR`.
    """
    forjado = copy.deepcopy(yaml.safe_load(_RATIFICATION.read_text(encoding="utf-8")))
    for fonte in forjado["fontes"].values():
        fonte.update({"ratificado": True, "revisor": "SME", "ratificado_em": "2026-08-09"})
    path = tmp_path / "auth-criteria-ratification.yaml"
    path.write_text(yaml.safe_dump(forjado, allow_unicode=True), encoding="utf-8")

    result = _all_green_worker(path).execute(dict(_GREEN_VARS))

    assert result["criterio_contratual_ok"] is False
    assert "CONTRATUAL_FONTE_NAO_RATIFICADA" in result["auto_criteria_falhas"]
    # ...e so ele: os outros tres passam, o que prova que a recusa vem do bloqueio declarado e
    # nao de algum outro criterio falhando antes.
    assert result["criterio_tecnico_ok"] is True
    assert result["criterio_financeiro_ok"] is True
    assert result["criterio_regulatorio_ok"] is True

    # A ligacao com a DMN, lida da tabela REAL: a regra favoravel exige `true` em cada um dos
    # booleanos calculados, e este resultado falha exatamente um deles.
    favourable = next(
        r for r in _rules(auto_approval_table) if '"AUTO_APROVAR"' in _entry_texts(r, "outputEntry")
    )
    # strict=True: with strict=False a 7th DMN input would silently be TRUNCATED off the zip
    # instead of failing this test directly (GK-criteria minor-2) — today's arity is exactly 6
    # names / 6 entries (_EXPECTED_DMN_INPUTS), so the lengths must match exactly.
    exigidos = [
        name
        for name, entry in zip(_EXPECTED_DMN_INPUTS, _entry_texts(favourable, "inputEntry"), strict=True)
        if entry == "true"
    ]
    assert "criterio_contratual_ok" in exigidos
    assert [name for name in exigidos if result[name] is not True] == ["criterio_contratual_ok"]


def test_remover_a_entrada_sem_cobertura_torna_a_ratificacao_efetiva(tmp_path: Path) -> None:
    """O caminho de resolucao, sem mudanca de codigo: com uma fonte de credenciamento real e a
    entrada removida do YAML (CODEOWNERS-gated), a MESMA ratificacao passa a valer. Sem este
    teste a supressao acima poderia ser um `return False` disfarcado."""
    forjado = copy.deepcopy(yaml.safe_load(_RATIFICATION.read_text(encoding="utf-8")))
    for fonte in forjado["fontes"].values():
        fonte.update({"ratificado": True, "revisor": "SME", "ratificado_em": "2026-08-09"})
    forjado.pop("criterios_nao_cobertos")
    path = tmp_path / "auth-criteria-ratification.yaml"
    path.write_text(yaml.safe_dump(forjado, allow_unicode=True), encoding="utf-8")

    result = _all_green_worker(path).execute(dict(_GREEN_VARS))
    assert result["criterio_contratual_ok"] is True
    assert result["auto_criteria_falhas"] == []


# ---------------------------------------------------------------------------------------------
# 5. AUTH-TETO-ZERO-STALE-CLAIM: D-07 closed 2026-08-25 — the "max_value_brl: 0 / D-07 aberto"
#    framing must not survive in the worker docstrings or the BPMN documentation text.
# ---------------------------------------------------------------------------------------------

_STALE_TETO_SUBSTRINGS = (
    "max_value_brl: 0",
    "max_value_brl e 0",
    "D-07 open",
    "D-07 em aberto",
    "D-07 aberto",
)


def test_live_ceiling_is_positive_precondition_for_the_stale_claim_fence() -> None:
    """Sanity precondition: this fence only makes sense while D-07 stays closed (ceiling > 0).
    If the tenant ceiling ever drops back to 0, the "nothing auto-approves" framing becomes true
    for the financial criterion again and the stale-claim assertions below must be revisited."""
    tenants = yaml.safe_load(_TENANTS_AMH.read_text(encoding="utf-8"))
    live_teto = tenants["overrides"]["authorization_approval"]["params"]["max_value_brl"]
    assert live_teto > 0, "D-07 reopened? test_worker_docstrings_and_bpmn_* must be re-derived"


def test_worker_docstrings_do_not_repeat_the_closed_zero_ceiling_claim() -> None:
    """AUTH-TETO-ZERO-STALE-CLAIM. `ValidateAutoCriteriaWorker` and `IssueAuthorizationWorker`
    docstrings used to claim `authorization_approval.max_value_brl: 0 (D-07 open/em aberto)` —
    stale since D-07 closed on 2026-08-25 (`tenants-amh.yaml` carries a positive ceiling). Revert
    either docstring to the old wording and this goes RED."""
    docstrings = "\n".join(
        doc for doc in (auth.ValidateAutoCriteriaWorker.__doc__, auth.IssueAuthorizationWorker.__doc__) if doc
    )
    for stale in _STALE_TETO_SUBSTRINGS:
        assert stale not in docstrings, f"stale claim {stale!r} resurfaced in a worker docstring"


def test_bpmn_documentation_does_not_repeat_the_closed_zero_ceiling_claim(
    bpmn_process: ET.Element,
) -> None:
    """Same gap, `SP-OP-AUTH-001` BPMN side: `BRT_AutoApproval`'s `camunda:documentation` used to
    read "...authorization_approval.max_value_brl e 0 (D-07)...". Revert it and this goes RED."""
    doc_texts = [(el.text or "") for el in bpmn_process.iter() if _local(el.tag) == "documentation"]
    joined = "\n".join(doc_texts)
    assert doc_texts, "no documentation elements found -- BPMN parsing regressed"
    for stale in _STALE_TETO_SUBSTRINGS:
        assert stale not in joined, f"stale claim {stale!r} resurfaced in BPMN documentation"
