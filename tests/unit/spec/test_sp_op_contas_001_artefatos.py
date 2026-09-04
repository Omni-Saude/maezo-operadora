"""Asserções ESTÁTICAS sobre os artefatos de SP-OP-CONTAS-001 — sem engine, no gate unitário.

Por que este módulo existe (VERIFY-PR4-CONTAS MAJOR-2). Estas verificações moravam em
`tests/integration/processes/test_sp_op_contas_001.py`, numa seção rotulada «sem engine;
varredura estática do XML» — mas o módulo inteiro carrega `pytestmark = pytest.mark.integration`,
então `make test` (`-m "not integration"`) as DESELECIONAVA. O resultado prático: a asserção sobre
o domínio de roteamento da `glosa_triage` continuou afirmando o vocabulário ANTIGO
(`{SEM_GLOSA, RECORRER, ANALISE_HUMANA}`) durante toda a reconstrução, e só apareceu numa janela
de engine — um teste que não precisa de engine ficou invisível para o gate que o cobriria em 0,1s.

O que vive aqui: qualquer proposição sobre os BYTES de `spec/processes/**` de SP-OP-CONTAS-001.
O que NÃO vive aqui: qualquer coisa que precise de instância, timer, worker ou history — isso
continua em `tests/integration/processes/test_sp_op_contas_001.py`.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_DMN_REASON = _REPO / "spec/processes/dmn/glosa_reason_normalization.dmn"
_DMN_CLASS = _REPO / "spec/processes/dmn/glosa_classification.dmn"
_DMN_TRIAGE = _REPO / "spec/processes/dmn/glosa_triage.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/contas_sla.dmn"

#: O domínio de roteamento que o redesenho de registro prescreve para `glosa_triage`
#: (REDESIGN-SP-OP-CONTAS-001.md:291 e :493) e que a tabela viva implementa. DOIS valores, não
#: três: `SEM_GLOSA` era a moldura do prestador («não houve glosa») e `RECORRER` é um ato do
#: RECORRENTE, não do pagador. O que sobra do lado da operadora é `PAGAR` (o único desfecho
#: automático possível, porque é o favorável) e `ANALISE_HUMANA`.
_DOMINIO_ROTEAMENTO_GLOSA_TRIAGE = frozenset({"PAGAR", "ANALISE_HUMANA"})

#: Variáveis humanas que as `conditionExpression` de `GW_DecisaoContas` leem e que NENHUM worker
#: escreve antes da User Task. Todas TÊM de ser inicializadas em `ST_PublishReceived`.
_VARS_DE_DECISAO_HUMANA = ("decisao_contas",)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _bpmn_root() -> ET.Element:
    return ET.parse(_BPMN).getroot()


def _publish_received_output_parameters() -> dict[str, str]:
    """`name -> texto` dos `camunda:outputParameter` de `ST_PublishReceived`."""
    for el in _bpmn_root().iter():
        if _local(el.tag) == "serviceTask" and el.get("id") == "ST_PublishReceived":
            return {
                str(p.get("name")): (p.text or "")
                for p in el.iter()
                if _local(p.tag) == "outputParameter" and p.get("name")
            }
    raise AssertionError("ST_PublishReceived nao existe no BPMN de SP-OP-CONTAS-001")


def _condition_expressions() -> dict[str, str]:
    """`sequenceFlow id -> texto da conditionExpression` (só os flows condicionais)."""
    out: dict[str, str] = {}
    for el in _bpmn_root().iter():
        if _local(el.tag) != "sequenceFlow":
            continue
        for child in el:
            if _local(child.tag) == "conditionExpression" and child.text:
                out[str(el.get("id"))] = child.text
    return out


# ---------------------------------------------------------------------------------------------
# DMN — forma e fail-safe
# ---------------------------------------------------------------------------------------------


def test_glosa_triage_sem_saida_de_glosa() -> None:
    """O domínio de roteamento da `glosa_triage` é EXATAMENTE `{PAGAR, ANALISE_HUMANA}`.

    A propriedade que importa é NEGATIVA e é o L0 hard: nenhuma saída desta tabela glosa. Não há
    `GLOSAR`, não há `ACEITAR`/`CONFIRMAR` (a moldura do prestador aceitando uma glosa alheia) e
    não há `RECORRER` (ato do recorrente, que a operadora não pratica contra si mesma). O único
    desfecho automático é o FAVORÁVEL ao prestador; tudo o mais vai a humano.
    """
    root = ET.parse(_DMN_TRIAGE).getroot()

    roteamentos: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_rule_first_output = val

    assert roteamentos == set(_DOMINIO_ROTEAMENTO_GLOSA_TRIAGE), (
        f"dominio de roteamento inesperado: {roteamentos} — o desenho de registro prescreve "
        f"{sorted(_DOMINIO_ROTEAMENTO_GLOSA_TRIAGE)} (REDESIGN-SP-OP-CONTAS-001.md:291,:493)"
    )
    juntos = " ".join(roteamentos)
    for proibido in ("ACEITAR", "CONFIRMAR", "GLOSAR", "RECORRER", "SEM_GLOSA"):
        assert proibido not in juntos, (
            f"'{proibido}' nao pode ser saida de DMN neste processo (L0 hard / perspectiva do pagador)"
        )
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_REASON, _DMN_CLASS, _DMN_TRIAGE, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


# ---------------------------------------------------------------------------------------------
# BPMN — o default fail-closed do gateway decisório tem de ser ALCANÇÁVEL
# ---------------------------------------------------------------------------------------------


def test_variaveis_de_decisao_humana_sao_inicializadas_no_primeiro_service_task() -> None:
    """CIB Seven 2.1.0 lança `Unknown property used in expression ... Cannot resolve identifier`
    quando uma `conditionExpression` lê uma variável que NUNCA foi setada — e o faz ANTES de cair
    no `default` do gateway. Sem esta inicialização o caso «decisão AUSENTE» não alcança
    `End_ErrContasDecisaoInvalida`: o `POST /task/{id}/complete` devolve 500 e a instância fica
    PARADA na User Task. O terminal de erro cobre «ausente OU desconhecida» (documentação do
    próprio end event e de `GW_DecisaoContas`); só o segundo caso funcionava.

    `""` é o valor certo: nenhuma das 5 condições do domínio casa com ele, então o token cai no
    default fail-closed. E não afrouxa o L0 hard — `""` não está em `{GLOSAR, PAGAR_PARCIAL}`,
    logo `registrar_glosa` continua recusando com `ERR_CONTAS_GLOSA_NOT_HUMAN`.

    Mesma mecânica, e pela mesma razão de engine, que SP-OP-RECURSO-001
    (`test_recurso.py::test_variaveis_de_decisao_humana_sao_inicializadas_no_primeiro_service_task`).
    """
    outputs = _publish_received_output_parameters()
    for var in _VARS_DE_DECISAO_HUMANA:
        assert outputs.get(var) == '${""}', (
            f'{var} tem de ser inicializada como ${{""}} em ST_PublishReceived; veio '
            f"{outputs.get(var)!r}. Sem isso o default fail-closed de GW_DecisaoContas e "
            "INALCANCAVEL para uma decisao ausente (a instancia trava na UT com HTTP 500)."
        )


def test_toda_variavel_lida_por_gateway_decisorio_esta_inicializada() -> None:
    """Fecha a CLASSE, não só a variável de hoje: qualquer identificador lido por uma
    `conditionExpression` de `GW_DecisaoContas` tem de estar inicializado em `ST_PublishReceived`
    — senão um autor futuro reintroduz o mesmo travamento com outro nome."""
    inicializadas = set(_publish_received_output_parameters())
    lidas: set[str] = set()
    for flow_id, expr in _condition_expressions().items():
        if not flow_id.startswith("Flow_GWDec_"):
            continue
        lidas.update(re.findall(r"\b([a-z_][a-z0-9_]*)\s*(?:==|!=)", expr))
    assert lidas, "nenhuma conditionExpression de GW_DecisaoContas encontrada — teste inerte"
    assert lidas <= inicializadas, (
        "variaveis lidas por GW_DecisaoContas sem inicializacao em ST_PublishReceived: "
        f"{sorted(lidas - inicializadas)}"
    )


def test_a_inicializacao_nao_casa_com_nenhuma_rota_de_acao() -> None:
    """O valor inicializado (`""`) não pode satisfazer NENHUMA condição de ação — se casasse, a
    inicialização teria criado um ato por omissão, exatamente o anti-padrão que o default
    fail-closed existe para impedir."""
    condicoes = _condition_expressions()
    assert condicoes, "nenhuma conditionExpression no BPMN — teste inerte"
    for flow_id, expr in condicoes.items():
        if not flow_id.startswith("Flow_GWDec_"):
            continue
        assert "== ''" not in expr.replace('"', "'"), (
            f"{flow_id} casa com a string vazia — a inicializacao viraria uma acao por omissao"
        )


def test_os_gateways_decisorios_tem_default_fail_closed() -> None:
    """Controle de não-vacuidade dos três testes acima: se `GW_DecisaoContas` perdesse o
    `default`, inicializar a variável não salvaria nada — o token ficaria sem saída."""
    defaults = {
        str(el.get("id")): el.get("default")
        for el in _bpmn_root().iter()
        if _local(el.tag) == "exclusiveGateway"
    }
    assert defaults.get("GW_DecisaoContas") == "Flow_GWDec_Invalida", (
        f"GW_DecisaoContas tem de ter default=Flow_GWDec_Invalida; veio {defaults.get('GW_DecisaoContas')!r}"
    )


# ---------------------------------------------------------------------------------------------
# Catalogo de erros — «declarado-e-nao-capturado» é ESTADO RATIFICADO, não entrada morta
# ---------------------------------------------------------------------------------------------

#: O catálogo `bpmn:error` de raiz do processo: `id -> errorCode`. Fixado para que uma remoção ou
#: um acréscimo silencioso apareça no gate unitário.
_CATALOGO_ESPERADO = {
    "Error_ContasLoteInvalido": "ERR_CONTAS_LOTE_INVALIDO",
    "Error_ContasGlosaNotHuman": "ERR_CONTAS_GLOSA_NOT_HUMAN",
    "Error_ContasDecisaoInvalida": "ERR_CONTAS_DECISAO_INVALIDA",
}

#: A ÚNICA entrada do catálogo que um `errorEventDefinition` referencia — e num throw-END, não num
#: boundary catch. As outras duas são deliberadamente não referenciadas (ADR-0030 §5).
_UNICA_ENTRADA_REFERENCIADA = "Error_ContasDecisaoInvalida"


def _catalogo_de_erros() -> dict[str, str]:
    return {
        str(el.get("id")): str(el.get("errorCode"))
        for el in _bpmn_root().iter()
        if _local(el.tag) == "error" and el.get("id")
    }


def _error_event_definitions() -> list[tuple[str, str]]:
    """`(tag local do elemento PAI, errorRef)` de cada `errorEventDefinition` do processo."""
    out: list[tuple[str, str]] = []
    for parent in _bpmn_root().iter():
        for child in parent:
            if _local(child.tag) == "errorEventDefinition" and child.get("errorRef"):
                out.append((_local(parent.tag), str(child.get("errorRef"))))
    return out


def test_catalogo_de_erros_e_declarado_e_nao_capturado() -> None:
    """Duas das três entradas do catálogo NÃO são referenciadas — e isso é ratificado, não morto.

    Este teste existe porque a leitura ingênua («entrada de catálogo sem `errorEventDefinition` =
    entrada morta = remover») é plausível, recorrente, e ERRADA aqui — e nada no repositório a
    impedia de ser executada. O que a impede:

    - ADR-0030 §2 põe a fonte da verdade do gate no BOUNDARY, não no catálogo: *«every
      `bpmn:error@errorCode` on an error boundary event attached to an external task»*. Uma entrada
      sem boundary é invisível para `scripts/ci/check_bpmn_error_allowlist.py` — não entra em
      `spec_codes`, logo nem a cláusula (c) (dead model) nem a (b) (raise não coberto) a alcançam.
      Removê-la não mudaria NENHUM resultado de gate; só apagaria a âncora documental.
    - ADR-0030 §5 nomeia o estado: *«a guard code with no modeled boundary (… cancel's
      `ERR_CANCELLATION_NOT_HUMAN`, declared-uncaught) → incident, unchanged. The L0 invariant holds
      identically either way — neither path performs the adverse action.»*
    - A emenda de ADR-0040 ao ADR-0030 cita `ERR_CONTAS_GLOSA_NOT_HUMAN` PELO NOME como
      *«Tier-3 declared-and-uncaught»*, que *«keep[s] raising `PermissionError` on the
      audited-incident path (never `WorkerBpmnError`/`bpmnError`)»*. Apagar a declaração tornaria
      falsa a descrição que o ADR faz do próprio artefato.
    - ADR-0030 §4: modelar o boundary ANTES de T-E converteria o *«guaranteed-human-visible
      incident»* num *«clean, silent end … no incident, no audit row, no notification»* — regressão
      de visibilidade sob a invariante HITL de não-negativa, não melhoria.
    """
    catalogo = _catalogo_de_erros()
    assert catalogo == _CATALOGO_ESPERADO, (
        f"o catálogo bpmn:error de SP-OP-CONTAS-001 mudou; veio {catalogo!r}. "
        "Acrescentar/remover uma entrada é decisão de processo (ADR-0030 §2/§5) — atualize o "
        "contrato e este teste junto, nunca só o BPMN."
    )

    referencias = _error_event_definitions()
    referenciados = {ref for _, ref in referencias}
    assert referenciados == {_UNICA_ENTRADA_REFERENCIADA}, (
        f"exatamente uma entrada do catálogo pode ser referenciada; vieram {sorted(referenciados)}"
    )

    # A referência é um throw-END (terminal técnico), NÃO um boundary catch.
    assert referencias == [("endEvent", _UNICA_ENTRADA_REFERENCIADA)], (
        f"`{_UNICA_ENTRADA_REFERENCIADA}` tem de ser referenciado por um throw-end; veio {referencias!r}"
    )


def test_contas_nao_tem_error_boundary_sobre_external_task() -> None:
    """CONTAS continua com ZERO error-boundary sobre external task — o estado que o censo do
    ADR-0030 registra para esta família (§Contexto: *«adequacao / contas / fraude have zero such
    catches»*), e que o contrato repete. Controle de não-vacuidade do teste acima: se alguém
    modelasse um boundary de erro aqui, a asserção de `_error_event_definitions` acima já quebraria,
    mas esta explicita QUAL propriedade estrutural está sendo protegida."""
    boundaries_de_erro = [
        str(el.get("id"))
        for el in _bpmn_root().iter()
        if _local(el.tag) == "boundaryEvent"
        and any(_local(child.tag) == "errorEventDefinition" for child in el)
    ]
    assert boundaries_de_erro == [], (
        f"SP-OP-CONTAS-001 não pode ter error-boundary; vieram {boundaries_de_erro}. "
        "Modelar um exige ratificação (ADR-0030 §4: pré-T-E isso troca incidente visível por fim "
        "silencioso) e passa por CODEOWNERS de docs/adr/."
    )
