"""INTERLOCK T1 (PROC-01 / EP-PROC-01a): the CPT-masked `carencia_check` can never quietly become effective.

`spec/processes/dmn/carencia_check.dmn` (FIRST-hit, :59) opens with a CPT block whose `tipo_procedimento`
is a wildcard (:89-108), so under `cpt_declarada = true` the 24h urgency ladder (:115-134) is unreachable:
`urgencia_emergencia / 30 / true` -> `r01` -> `false / 700`. The fix is the regulatory owner's act
(ADR-0028 :241; `carencia-check-shadow-candidate.yaml`), NOT engineering's. What engineering owns is
making sure that, until that act (or the table's retirement for the rco K1 package), neither of the two
outside acts that would turn the masked verdict live can land:

  A1  ratifying `carencia_check` in `auth-criteria-ratification.yaml` — refused in CI (A1a) and, through
      the restrictive `criterios_nao_cobertos.t1_mascaramento_cpt_urgencia` entry, in RUNTIME (A1b);
  A2  producing `tipo_procedimento` / `dias_desde_adesao` / `cpt_declarada` for SP-OP-AUTH-001 (A2a),
      with a meta-guard that this file rides every agent's CI trail (A2b);
  A3  and, whatever the table decides, `false` stays "route to a human" (A3a), also when composed with
      `auth_admissibility` (A3b). A3c — the same repro end to end on the REAL engine — lives in
      `tests/integration/processes/test_sp_op_auth_001.py` (integration lane).

The interlock lifts ONLY by PROC-01 condition (i) or (ii); `tests.support.carencia_t1_interlock` is the
single place that decides, and `test_o_interlock_so_e_levantado_pela_condicao_i_ou_ii` pins that. Every
guard here has a negative case that INJECTS the violation and asserts the refusal. No `xfail`, no
conditional `skip`, no engine mock: tables are read from the real artefacts by the FIRST-hit reader.

CI trail markers (A2b, `scripts/ci/trilha.py::testes_do_agente`): a PR touching only one agent's code
must still run this file, because an agent graph is the natural place for a producer of the inputs.
  agents/andre/ agents/beatriz/ agents/carolina/ agents/fernando/ agents/gustavo/
  agents/helena/ agents/lucas/ agents/marina/ agents/rafael/ agents/valentina/
"""

from __future__ import annotations

import copy
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.ci.trilha import agentes_existentes, classificar
from scripts.ci.trilha import testes_do_agente as _testes_do_agente

from maezo.tools.workers.auth_criteria import load_criteria_sources
from tests.support.carencia_t1_interlock import (
    ADMISSIBILIDADE_REL,
    AUTH_BPMN_REL,
    AUTH_CONTRATO_REL,
    AUTH_WORKER_REL,
    CANDIDATO_NOME,
    CANDIDATO_REL,
    ENGINE_SCHEMAS_JAVA_REL,
    ENGINE_SCHEMAS_REL,
    ENTRADA_BLOQUEIO,
    FONTE,
    GRADE_DIAS,
    MANIFESTO_RATIFICACAO_REL,
    RATCHET_CPT_DIAS,
    RATCHET_TIPO,
    RCO_LOCK_REL,
    SHA256_TABELA_PRE_FIX,
    TABELA_REL,
    TIPO_URGENCIA,
    TOKEN_ADMISSIBILIDADE,
    TOKEN_BLOQUEIO,
    TOKEN_ENTRADAS,
    TOKEN_L0,
    TOKEN_RATIFICACAO,
    TOKEN_TRILHA,
    InterlockT1ViolationError,
    acusar_produtores_de_entradas,
    condicao_i_satisfeita,
    condicao_ii_satisfeita,
    interlock_t1_vigente,
    marcadores_de_trilha_ausentes,
    mascaramento_t1_ativo,
    tabela_do_criterio_regulatorio,
    valores_de_carencia_emitidos,
    verificar_bloqueio_declarado,
    verificar_interlock_de_entradas,
    verificar_interlock_de_ratificacao,
    violacoes_composicao_admissibilidade,
    violacoes_l0_carencia,
)
from tests.support.dmn_first_hit import (
    REPO_ROOT,
    evaluate,
    live_table_digest,
    read_candidate_table,
    read_live_table,
)

_VIVA = REPO_ROOT / TABELA_REL
_MANIFESTO = REPO_ROOT / MANIFESTO_RATIFICACAO_REL
_CANDIDATO = REPO_ROOT / CANDIDATO_REL
_ADMISSIBILIDADE = REPO_ROOT / ADMISSIBILIDADE_REL
_ESTE_ARQUIVO = Path(__file__).resolve()
_ESTE_ARQUIVO_REL = _ESTE_ARQUIVO.relative_to(REPO_ROOT).as_posix()

_RATIFICACAO_COMPLETA = {"ratificado": True, "revisor": "x", "ratificado_em": "2026-11-16"}


# =================================================================================================
# Fixture builders — every forgery is a COPY in tmp_path; the real tree is never written
# =================================================================================================


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _por_id(raiz: ET.Element, ident: str) -> ET.Element:
    return next(el for el in raiz.iter() if el.get("id") == ident)


def _texto(elemento: ET.Element) -> ET.Element:
    return next(filho for filho in elemento if _local(filho.tag) == "text")


def _copia_mutada(origem: Path, destino: Path, mutacao: Callable[[ET.Element], None]) -> Path:
    arvore = ET.parse(origem)
    mutacao(arvore.getroot())
    destino.parent.mkdir(parents=True, exist_ok=True)
    arvore.write(destino, encoding="utf-8", xml_declaration=True)
    return destino


def _aplicar_diff_do_candidato(raiz: ET.Element) -> None:
    """The candidate's whole diff (option (a) of DECISAO-A): r03/r04 to the top, their CPT cell `-`."""
    tabela = next(el for el in raiz.iter() if _local(el.tag) == "decisionTable")
    regras = {el.get("id"): el for el in tabela if _local(el.tag) == "rule"}
    for regra in ("r03", "r04"):
        tabela.remove(regras[regra])
    posicao = list(tabela).index(regras["r01"])
    tabela.insert(posicao, regras["r04"])
    tabela.insert(posicao, regras["r03"])
    for celula in ("r03i3", "r04i3"):
        _texto(_por_id(raiz, celula)).text = "-"


def _tabela_corrigida(destino: Path) -> Path:
    return _copia_mutada(_VIVA, destino, _aplicar_diff_do_candidato)


def _manifesto_forjado(tmp_path: Path, nome: str, mutar: Callable[[dict[str, Any]], None]) -> Path:
    data = copy.deepcopy(yaml.safe_load(_MANIFESTO.read_text(encoding="utf-8")))
    mutar(data)
    path = tmp_path / nome
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _raiz_vigente(tmp_path: Path) -> Path:
    """A tree where the interlock stands BY CONSTRUCTION, whatever the live table looks like on the day
    this runs: the table is present (so (ii) cannot hold) and no ratified candidate is archived (so (i)
    cannot hold). Negative cases run against it so they keep proving the refusal after the owner's act."""
    raiz = tmp_path / "raiz_vigente"
    _copiar(raiz, TABELA_REL)
    return raiz


def _copiar(raiz: Path, *rels: str) -> None:
    for rel in rels:
        (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, raiz / rel)


def _arquivar_candidato(raiz: Path, destino_rel: str, **sobrescrever: Any) -> Path:
    data = yaml.safe_load(_CANDIDATO.read_text(encoding="utf-8"))
    data.update(_RATIFICACAO_COMPLETA | {"revisor": "juridico/regulatorio + medico auditor"})
    for chave, valor in sobrescrever.items():
        if chave == "sha256":
            data["tabela_viva"]["sha256"] = valor
        else:
            data[chave] = valor
    destino = raiz / destino_rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destino


# =================================================================================================
# A0 — the predicate is not vacuous
# =================================================================================================


def test_predicado_mascaramento_t1_discrimina_tabela_viva_de_tabela_corrigida(tmp_path: Path) -> None:
    """Non-vacuity of every guard below: the predicate is True on the live table and False on a COPY of
    it with the candidate's diff applied — so it can neither be always-True nor always-False."""
    viva = read_live_table(_VIVA)
    corrigida = read_live_table(_tabela_corrigida(tmp_path / "carencia_check.dmn"))

    # Premise, derived from the artefact (GUARD-TEST-SPECS §0.2): the repro lands on r01.
    repro = evaluate(
        viva, {"tipo_procedimento": TIPO_URGENCIA, "dias_desde_adesao": 30, "cpt_declarada": True}
    )
    assert (repro.regra, repro.saidas["carencia_cumprida"], repro.saidas["prazo_restante_dias"]) == (
        "r01",
        False,
        700,
    )

    assert mascaramento_t1_ativo(viva) is True
    assert mascaramento_t1_ativo(corrigida) is False
    # The corrected copy is exactly the candidate's rule set, judged by what it decides.
    assert mascaramento_t1_ativo(read_candidate_table(_CANDIDATO, viva)) is False
    # The copy decides like the candidate on the repro: `(true, 0)`, never an invented value.
    corrigido = evaluate(
        corrigida, {"tipo_procedimento": TIPO_URGENCIA, "dias_desde_adesao": 30, "cpt_declarada": True}
    )
    assert (corrigido.saidas["carencia_cumprida"], corrigido.saidas["prazo_restante_dias"]) == (True, 0)


def test_o_digest_pre_fix_e_o_da_tabela_viva_e_o_do_candidato() -> None:
    """Condition (i) binds the ratified candidate to `SHA256_TABELA_PRE_FIX`. Today that IS the live
    table's digest and the candidate's own `tabela_viva.sha256` — three statements of one fact."""
    assert live_table_digest(_VIVA) == SHA256_TABELA_PRE_FIX
    assert (
        yaml.safe_load(_CANDIDATO.read_text(encoding="utf-8"))["tabela_viva"]["sha256"]
        == SHA256_TABELA_PRE_FIX
    )


# =================================================================================================
# The lifting condition — (i) or (ii), and nothing else
# =================================================================================================


def test_hoje_o_interlock_esta_vigente_e_nenhuma_condicao_de_levantamento_vale() -> None:
    """The recorded state at EP-PROC-01a: masking live, candidate unratified, table not retired."""
    assert condicao_i_satisfeita() is False
    assert condicao_ii_satisfeita() is False
    assert interlock_t1_vigente() is True
    # Non-vacuity of the (ii) resolver: it reads the REAL consumer's key from source.
    assert tabela_do_criterio_regulatorio() == "carencia_check"


def test_o_interlock_so_e_levantado_pela_condicao_i_ou_ii(tmp_path: Path) -> None:
    """Every near-miss keeps the interlock shut; only the complete (i) or the complete (ii) lifts it."""

    def raiz(nome: str, *, tabela: str | None = "viva") -> Path:
        r = tmp_path / nome
        _copiar(r, AUTH_WORKER_REL, RCO_LOCK_REL)
        if tabela == "viva":
            _copiar(r, TABELA_REL)
        elif tabela == "corrigida":
            _tabela_corrigida(r / TABELA_REL)
        return r

    arquivo = "docs/decisions/carencia/" + CANDIDATO_NOME

    # --- condition (i) -------------------------------------------------------------------------
    assert interlock_t1_vigente(raiz("i_nada")) is True
    assert interlock_t1_vigente(raiz("i_so_tabela_corrigida", tabela="corrigida")) is True, (
        "a corrected table WITHOUT the archived ratified candidate must not lift — no engineering valve"
    )
    r = raiz("i_candidato_ratificado_tabela_ainda_mascara")
    _arquivar_candidato(r, arquivo)
    assert interlock_t1_vigente(r) is True
    r = raiz("i_placeholder", tabela="corrigida")
    _arquivar_candidato(r, arquivo, revisor="PLACEHOLDER_DONO_REGULATORIO")
    assert interlock_t1_vigente(r) is True
    r = raiz("i_nao_arquivado", tabela="corrigida")
    _arquivar_candidato(r, CANDIDATO_REL)
    assert interlock_t1_vigente(r) is True, "a candidate still in spec/processes/dmn/ is not archived"
    r = raiz("i_digest_de_outra_tabela", tabela="corrigida")
    _arquivar_candidato(r, arquivo, sha256="0" * 64)
    assert interlock_t1_vigente(r) is True
    r = raiz("i_completa", tabela="corrigida")
    _arquivar_candidato(r, arquivo)
    assert condicao_i_satisfeita(r) is True
    assert interlock_t1_vigente(r) is False

    # --- condition (ii) ------------------------------------------------------------------------
    def aposentar(r: Path, *, chave: str, estado: str) -> None:
        worker = r / AUTH_WORKER_REL
        texto = worker.read_text(encoding="utf-8")
        novo = texto.replace('_DMN_CARENCIA_CHECK = "carencia_check"', f'_DMN_CARENCIA_CHECK = "{chave}"')
        assert novo != texto, "the consumer anchor moved — update this forgery"
        worker.write_text(novo, encoding="utf-8")
        lock = r / RCO_LOCK_REL
        lock.write_text(
            lock.read_text(encoding="utf-8").replace('"sem_pacote"', f'"{estado}"'), encoding="utf-8"
        )

    r = raiz("ii_so_tabela_removida", tabela=None)
    assert interlock_t1_vigente(r) is True
    r = raiz("ii_lock_sem_pacote", tabela=None)
    aposentar(r, chave="rco_geral_cpt", estado="sem_pacote")
    assert interlock_t1_vigente(r) is True
    r = raiz("ii_consumidor_nao_rco", tabela=None)
    aposentar(r, chave="carencia_check_v2", estado="fixado")
    assert interlock_t1_vigente(r) is True
    r = raiz("ii_tabela_ainda_presente")
    aposentar(r, chave="rco_geral_cpt", estado="fixado")
    assert interlock_t1_vigente(r) is True
    r = raiz("ii_completa", tabela=None)
    aposentar(r, chave="rco_geral_cpt", estado="fixado")
    assert condicao_ii_satisfeita(r) is True
    assert interlock_t1_vigente(r) is False

    # Lifting is BY CONDITION, not by code: the same forged ratification refused in a standing tree
    # (A1a below) passes once the tree satisfies (i).
    forjado = _manifesto_forjado(tmp_path, "ratificado.yaml", _ratificar_sem_bloqueio)
    verificar_interlock_de_ratificacao(forjado, raiz=tmp_path / "i_completa")
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_RATIFICACAO):
        verificar_interlock_de_ratificacao(forjado, raiz=tmp_path / "i_so_tabela_corrigida")


# =================================================================================================
# A1a — ratification interlock, CI layer
# =================================================================================================


def _ratificar_sem_bloqueio(data: dict[str, Any]) -> None:
    """The forged act: ratify `carencia_check` AND delete A1b's entry, so A1a is tested on its own —
    it must hold even if the restrictive data entry is removed (EP-PROC-01a acceptance 5)."""
    data["fontes"][FONTE].update(_RATIFICACAO_COMPLETA)
    del data["criterios_nao_cobertos"][ENTRADA_BLOQUEIO]


def test_carencia_check_nao_pode_ser_ratificado_enquanto_tabela_viva_mascara(tmp_path: Path) -> None:
    # The real manifest against the real tree.
    verificar_interlock_de_ratificacao(_MANIFESTO)
    assert load_criteria_sources(_MANIFESTO).is_ratified(FONTE) is False

    # NEGATIVE: the pure-DATA flip `auth-criteria-ratification.yaml:26-35` describes.
    forjado = _manifesto_forjado(tmp_path, "forjado.yaml", _ratificar_sem_bloqueio)
    assert load_criteria_sources(forjado).is_ratified(FONTE) is True, "premise: the forgery really ratifies"
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_RATIFICACAO):
        verificar_interlock_de_ratificacao(forjado, raiz=_raiz_vigente(tmp_path))


# =================================================================================================
# A1b — ratification interlock, DATA layer (runtime refusal)
# =================================================================================================


def test_t1_bloqueio_de_ratificacao_declarado_e_efetivo_enquanto_mascara(tmp_path: Path) -> None:
    # (1) the real manifest declares the block and the real loader honours it.
    verificar_bloqueio_declarado(_MANIFESTO)
    assert ENTRADA_BLOQUEIO in load_criteria_sources(_MANIFESTO).blocked_by_uncovered[FONTE]

    def ratificar_tudo(data: dict[str, Any]) -> None:
        for fonte in data["fontes"].values():
            fonte.update(_RATIFICACAO_COMPLETA)

    def ratificar_tudo_sem_a_entrada(data: dict[str, Any]) -> None:
        ratificar_tudo(data)
        del data["criterios_nao_cobertos"][ENTRADA_BLOQUEIO]

    # (2) NEGATIVE: everything ratified, entry present -> the runtime loader refuses carencia_check.
    com_entrada = load_criteria_sources(_manifesto_forjado(tmp_path, "com.yaml", ratificar_tudo))
    assert FONTE in com_entrada.ratified, "premise: the three fields really are filled in"
    assert com_entrada.is_ratified(FONTE) is False
    # (3) CONTROL: the identical forgery without the entry -> ratified. It is the ENTRY that refuses.
    sem_entrada_path = _manifesto_forjado(tmp_path, "sem.yaml", ratificar_tudo_sem_a_entrada)
    assert load_criteria_sources(sem_entrada_path).is_ratified(FONTE) is True

    # NEGATIVE: deleting the entry while the interlock stands is refused with the stable token.
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_BLOQUEIO):
        verificar_bloqueio_declarado(sem_entrada_path, raiz=_raiz_vigente(tmp_path))

    # NEGATIVE: an entry that blocks the WRONG source is not this block.
    def bloqueio_errado(data: dict[str, Any]) -> None:
        data["criterios_nao_cobertos"][ENTRADA_BLOQUEIO]["bloqueia_ratificacao_de"] = "dut_rol_coverage"

    errado = _manifesto_forjado(tmp_path, "errado.yaml", bloqueio_errado)
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_BLOQUEIO):
        verificar_bloqueio_declarado(errado, raiz=_raiz_vigente(tmp_path))


# =================================================================================================
# A2a — no producer of the three inputs
# =================================================================================================


def test_nenhum_produtor_de_entradas_da_carencia_enquanto_tabela_viva_mascara() -> None:
    verificar_interlock_de_entradas()
    if condicao_i_satisfeita():
        # Post-condition of EP-PROC-01c (B1): with the candidate applied, urgency under CPT reads
        # the 24h ladder — `(true, 0)` from day 1, `(false, 1)` on day 0. Never an invented value.
        viva = read_live_table(_VIVA)
        for dias, esperado in ((0, (False, 1)), (30, (True, 0))):
            saidas = evaluate(
                viva, {"tipo_procedimento": TIPO_URGENCIA, "dias_desde_adesao": dias, "cpt_declarada": True}
            ).saidas
            assert (saidas["carencia_cumprida"], saidas["prazo_restante_dias"]) == esperado


def _arvore_de_produtores(tmp_path: Path) -> Path:
    """A copy of exactly the files the A2a scanner reads — clean, so the control yields zero findings."""
    raiz = tmp_path / "arvore"
    _copiar(
        raiz,
        TABELA_REL,
        ENGINE_SCHEMAS_REL,
        ENGINE_SCHEMAS_JAVA_REL,
        AUTH_BPMN_REL,
        AUTH_CONTRATO_REL,
        *sorted(RATCHET_CPT_DIAS | RATCHET_TIPO),
    )
    return raiz


def test_o_scanner_de_produtores_acusa_tres_produtores_plantados(tmp_path: Path) -> None:
    """Non-vacuity of A2a: the SAME scanner, on a planted tree, accuses each planted producer."""
    raiz = _arvore_de_produtores(tmp_path)
    assert acusar_produtores_de_entradas(raiz) == (), "control: the clean copy has no finding"

    # Plant 1 — a gateway module that writes the input.
    gateway = raiz / "src/maezo/gateway/x.py"
    gateway.write_text('payload = {"cpt_declarada": True}\n', encoding="utf-8")
    # Plant 2 — the AUTH BPMN maps the input into a task.
    bpmn = raiz / AUTH_BPMN_REL
    texto = bpmn.read_text(encoding="utf-8")
    plantado = texto.replace(
        "<camunda:inputOutput>",
        '<camunda:inputOutput><camunda:inputParameter name="cpt_declarada">${true}</camunda:inputParameter>',
        1,
    )
    assert plantado != texto, "the BPMN no longer has a camunda:inputOutput to plant into"
    bpmn.write_text(plantado, encoding="utf-8")
    # Plant 3 — the typed start contract of Autorizacao_Previa declares it.
    schemas = raiz / ENGINE_SCHEMAS_REL
    schemas.write_text(
        schemas.read_text(encoding="utf-8")
        + '\nRAFAEL_START_PLANTADO = _agent("rafael", "AUTH", "Autorizacao_Previa", "_contract_variables",'
        + ' _fields("tipo_procedimento cpt_declarada", ValueKind.BOOLEAN))\n',
        encoding="utf-8",
    )

    acusacoes = acusar_produtores_de_entradas(raiz)
    assert {a.arquivo for a in acusacoes} == {"src/maezo/gateway/x.py", AUTH_BPMN_REL, ENGINE_SCHEMAS_REL}
    assert {a.verificacao for a in acusacoes} == {"ratchet", "bpmn", "start-python"}
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_ENTRADAS):
        verificar_interlock_de_entradas(raiz)


def test_o_scanner_acusa_o_contrato_que_ganha_fonte_e_o_ratchet_que_encolhe(tmp_path: Path) -> None:
    """Checks (3) and (4)-shrink: the spec-first first symptom — a contract row losing "Sem fonte hoje" —
    is a finding, and so is a frozen file silently dropping out of the ratchet."""
    raiz = _arvore_de_produtores(tmp_path)
    contrato = raiz / AUTH_CONTRATO_REL
    linhas = contrato.read_text(encoding="utf-8").splitlines(keepends=True)
    linhas = [
        linha.replace("**Sem fonte hoje**", "Fonte: DPS (perna DPP/DS)")
        if linha.startswith("| `cpt_declarada` |")
        else linha
        for linha in linhas
    ]
    contrato.write_text("".join(linhas), encoding="utf-8")
    (raiz / "src/maezo/platform/evidence/dmn_sweep.py").write_text("# esvaziado\n", encoding="utf-8")

    acusacoes = acusar_produtores_de_entradas(raiz)
    assert {(a.verificacao, a.arquivo.split(":")[0]) for a in acusacoes} == {
        ("contrato", AUTH_CONTRATO_REL),
        ("ratchet", "src/maezo/platform/evidence/dmn_sweep.py"),
    }
    with pytest.raises(InterlockT1ViolationError, match=TOKEN_ENTRADAS):
        verificar_interlock_de_entradas(raiz)


# =================================================================================================
# A2b — this file rides every agent's CI trail
# =================================================================================================


def test_interlock_de_entradas_e_selecionado_pela_trilha_de_todo_agente() -> None:
    # The agents that HAVE a trail, as the real classifier decides it: a PR touching only
    # `src/maezo/agents/<id>/...` is routed to that agent's trail. (`_template` has an `__init__.py`
    # but the classifier never treats it as an agent, so it has no trail to ride.)
    existentes = agentes_existentes()
    agentes = {
        agente
        for agente in existentes
        if classificar([f"src/maezo/agents/{agente}/graph.py"], existentes)["agentes"] == [agente]
    }
    assert {"rafael", "helena", "lucas"} <= agentes, "premise: the real agent roster was read"
    texto = _ESTE_ARQUIVO.read_text(encoding="utf-8")
    ausentes = marcadores_de_trilha_ausentes(texto, agentes)
    assert not ausentes, f"{TOKEN_TRILHA}: add these markers to the module docstring: {ausentes}"
    # The property itself, through the real selector — not just the text this file contains.
    for agente in sorted(agentes):
        assert _ESTE_ARQUIVO_REL in _testes_do_agente(agente), f"{TOKEN_TRILHA}: not on {agente}'s trail"

    # NEGATIVE: an agent added to the roster without a marker here is accused. The id is assembled
    # at runtime so its marker can never appear in this file's own text.
    fantasma = "".join(("fan", "tasma"))
    assert marcadores_de_trilha_ausentes(texto, {*agentes, fantasma}) == [f"agents/{fantasma}/"]


# =================================================================================================
# A3a — L0 over the whole input space, live table AND candidate
# =================================================================================================


def test_carencia_check_todo_veredito_false_e_rota_humana_em_todo_o_espaco(tmp_path: Path) -> None:
    viva = read_live_table(_VIVA)
    candidato = read_candidate_table(_CANDIDATO, viva)
    assert violacoes_l0_carencia(viva) == [], TOKEN_L0
    assert violacoes_l0_carencia(candidato) == [], TOKEN_L0

    # NEGATIVE (a): a decisive extra output `acao = "NEGAR"` on every rule.
    def saida_negar(raiz: ET.Element) -> None:
        tabela = next(el for el in raiz.iter() if _local(el.tag) == "decisionTable")
        saidas = [el for el in tabela if _local(el.tag) == "output"]
        nova = ET.Element(saidas[-1].tag, {"id": "out_acao", "name": "acao", "typeRef": "string"})
        tabela.insert(list(tabela).index(saidas[-1]) + 1, nova)
        for regra in (el for el in tabela if _local(el.tag) == "rule"):
            entrada = ET.SubElement(regra, saidas[-1].tag.replace("output", "outputEntry"))
            ET.SubElement(
                entrada, _texto(next(el for el in regra if _local(el.tag) == "outputEntry")).tag
            ).text = '"NEGAR"'

    negar = read_live_table(_copia_mutada(_VIVA, tmp_path / "negar.dmn", saida_negar))
    violacoes = violacoes_l0_carencia(negar)
    assert violacoes and any("declared outputs" in v for v in violacoes), violacoes

    # NEGATIVE (b): a `false` rule whose `fonte` drops the L0 instruction to the auditor.
    def sem_token(raiz: ET.Element) -> None:
        texto = _texto(_por_id(raiz, "r01o3"))
        texto.text = (texto.text or "").replace("Nunca negativa automatica.", "")

    sem = read_live_table(_copia_mutada(_VIVA, tmp_path / "sem_token.dmn", sem_token))
    violacoes = violacoes_l0_carencia(sem)
    assert violacoes and all(v.startswith("r01 @") for v in violacoes), violacoes

    # NEGATIVE (c): the same omission in the CANDIDATE's rules — the existing candidate comparisons
    # ignore `fonte` on purpose, so before this guard such an edit would have passed.
    data = yaml.safe_load(_CANDIDATO.read_text(encoding="utf-8"))
    alvo = next(r for r in data["regras_candidatas"] if r["id"] == "c_urgencia_nao_cumprida")
    alvo["saidas"]["fonte"] = alvo["saidas"]["fonte"].replace("Nunca negativa automatica.", "")
    forjado = tmp_path / CANDIDATO_NOME
    forjado.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    violacoes = violacoes_l0_carencia(read_candidate_table(forjado, viva))
    assert violacoes and all(v.startswith("c_urgencia_nao_cumprida @") for v in violacoes), violacoes


# =================================================================================================
# A3b — composition with auth_admissibility is never adverse
# =================================================================================================


def test_veredito_de_carencia_composto_com_admissibilidade_nunca_e_adverso(tmp_path: Path) -> None:
    viva = read_live_table(_VIVA)
    candidato = read_candidate_table(_CANDIDATO, viva)
    admissibilidade = read_live_table(_ADMISSIBILIDADE)
    # Non-vacuity: both tables really emit both verdicts across the grid.
    assert valores_de_carencia_emitidos(viva) == valores_de_carencia_emitidos(candidato) == {True, False}
    assert violacoes_composicao_admissibilidade([viva, candidato], admissibilidade) == [], (
        TOKEN_ADMISSIBILIDADE
    )

    # NEGATIVE: `r3` (carencia nao cumprida) rewired to a denial.
    def r3_nega(raiz: ET.Element) -> None:
        _texto(_por_id(raiz, "r3o1")).text = '"NEGADA"'

    adversa = read_live_table(_copia_mutada(_ADMISSIBILIDADE, tmp_path / "auth_admissibility.dmn", r3_nega))
    violacoes = violacoes_composicao_admissibilidade([viva], adversa)
    assert violacoes and all("-> r3 " in v and "'NEGADA'" in v for v in violacoes), violacoes

    # NEGATIVE: a denial on a cell where carencia is NOT what routes (`r1`, procedure outside the
    # authorization list) — caught by the "never adverse" clause alone, not by the SEGUE_ANALISE one.
    def r1_nega(raiz: ET.Element) -> None:
        _texto(_por_id(raiz, "r1o1")).text = '"NEGADA"'

    adversa_r1 = read_live_table(_copia_mutada(_ADMISSIBILIDADE, tmp_path / "admissibility_r1.dmn", r1_nega))
    violacoes = violacoes_composicao_admissibilidade([viva], adversa_r1)
    assert violacoes and all("-> r1 " in v and "adverse" in v for v in violacoes), violacoes


def test_a_grade_de_dias_cobre_toda_fronteira_declarada_pela_tabela() -> None:
    """The predicate's grid must straddle every day boundary the live table declares, or a masking
    confined to an unprobed day would read as 'no masking'."""
    viva = read_live_table(_VIVA)
    coluna = viva.input_names.index("dias_desde_adesao")
    fronteiras = {
        int(entrada.lstrip("<>= ")) for regra in viva.rules if (entrada := regra.inputs[coluna]) != "-"
    }
    assert fronteiras == {1, 180, 300, 730}
    for dia in fronteiras:
        assert {dia - 1, dia, dia + 1} <= set(GRADE_DIAS), dia
