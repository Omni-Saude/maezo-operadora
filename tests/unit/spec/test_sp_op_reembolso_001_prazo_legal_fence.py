"""E-SF-29 (EP-PROC-06a, parte 1) — cercas do prazo do reembolso (SP-OP-REEMBOLSO-001).

Regra da casa (AGENTS.md): prazo regulatório vive em timer BPMN; regra determinística vive em DMN;
nunca em Python ou prompt. Estas cercas valem HOJE, antes do relógio legal de pagamento
(Lei 9.656 art. 12, VI), que é a parte 3 e traz o próprio teste de aceite (C4):

- C1: todo timer do processo é parametrizado por uma saída de DMN avaliada no próprio BPMN, salvo
  o que estiver listado em `_TIMERS_LITERAIS_CONHECIDOS` (dívida declarada, que só encolhe);
- C2: nenhum prazo do reembolso em Python, em texto ao beneficiário ou em texto de agente;
- C3: um disparo de timer, até chegar a uma tarefa humana, só passa pelo que está em
  `_PERMITIDO_APOS_TIMER` (alerta interno e evento de estouro de SLA): nunca pagamento, aprovação,
  negativa, redução, desfecho ou aviso ao beneficiário. A varredura entra e sai de subprocessos.

Leitura estática dos artefatos versionados; nenhum engine, nenhum dado real.
"""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_BPMN = _ROOT / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN_DIR = _ROOT / "spec/processes/dmn"

_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}
_CAMUNDA = "{http://camunda.org/schema/1.0/bpmn}"
_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"

#: C1 — DÍVIDA: timers com duração literal no BPMN, com o valor pinado e o motivo. Só encolhe: se o
#: valor mudar, ou o timer passar a vir de DMN, o teste de dívida falha e a entrada sai (ou é revista).
_TIMERS_LITERAIS_CONHECIDOS: dict[str, tuple[str, str]] = {
    "TD_PrazoPendencia": (
        "P5D",
        "DÍVIDA: o prazo da pendência (espera pela documentação pedida ao beneficiário) está fixo no "
        "BPMN e 'DRAFT/verify'; pela regra da casa deveria vir de DMN. Não é o prazo legal de pagamento, "
        "e ao vencer decide um humano (UT_DecidirPendenciaExpirada)",
    ),
}

#: C2 — durações em Python de arquivos do reembolso que NÃO são prazo do processo, cada uma com o motivo.
_DURACOES_PYTHON_CONHECIDAS: dict[tuple[str, str], str] = {
    ("src/maezo/platform/webhooks/whatsapp/reembolso_turno.py", "VALIDADE_DA_COLETA"): (
        "janela de atendimento da conversa (24h da Meta), não prazo do reembolso"
    ),
    ("src/maezo/platform/webhooks/whatsapp/reembolso_turno.py", "VALIDADE_DEPOIS_DE_ABERTO"): (
        "por quanto tempo o protocolo segue consultável na conversa, não prazo do reembolso"
    ),
}

_ISO_DURACAO = re.compile(r"^-?P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(\d+H)?(\d+M)?(\d+S)?)?$")
#: C2 — constantes inteiras de prazo em Python do reembolso que NÃO são prazo, com o motivo.
_INTEIROS_PYTHON_CONHECIDOS: dict[tuple[str, str], str] = {
    ("src/maezo/tools/workers/reembolso.py", "DIAS_NAO_CALCULAVEL"): (
        "sentinela (-1) de 'dias desde o atendimento' não calculável, não prazo"
    ),
}
_NOME_DE_PRAZO = re.compile(r"PRAZO|DIAS|SLA|VENCIMENTO|DEADLINE", re.IGNORECASE)
_DIAS_EM_TEXTO = re.compile(
    r"\b(\d+|uma?|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|quinze|vinte|trinta|sessenta|noventa)"
    r"\s*(\(\s*[^\W\d_]+\s*\)\s*)?(dias?|horas?|meses|m[eê]s|semanas?)\b",
    re.IGNORECASE,
)
_EXPRESSAO_DMN = re.compile(r"^\$\{(\w+)\.(\w+)\}$")


def _process() -> ET.Element:
    process = ET.parse(_BPMN).getroot().find("bpmn:process", _NS)
    if process is None:
        pytest.fail("SP-OP-REEMBOLSO-001 sem bpmn:process")
    return process


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _nodes(process: ET.Element) -> dict[str, ET.Element]:
    return {
        el.get("id", ""): el
        for el in process.iter()
        if el.get("id") and _local(el.tag) not in {"sequenceFlow", "process"} and el.tag.startswith(_BPMN_NS)
    }


def _timers(process: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    """(evento que carrega o timer, timerEventDefinition)."""
    found = []
    for event in process.iter():
        if _local(event.tag) in {"boundaryEvent", "intermediateCatchEvent", "startEvent"}:
            for definition in event.findall("bpmn:timerEventDefinition", _NS):
                found.append((event, definition))
    return found


def _timer_expression(definition: ET.Element) -> str:
    texts = [
        (child.text or "").strip()
        for child in definition
        if _local(child.tag) in {"timeDuration", "timeDate", "timeCycle"}
    ]
    if len(texts) != 1:
        pytest.fail(f"timer {definition.get('id')} sem exatamente uma expressão de tempo: {texts}")
    return texts[0]


def _dmn_outputs(decision_ref: str) -> set[str]:
    for path in sorted(_DMN_DIR.glob("*.dmn")):
        root = ET.parse(path).getroot()
        for decision in root.iter():
            if _local(decision.tag) == "decision" and decision.get("id") == decision_ref:
                return {out.get("name", "") for out in decision.iter() if _local(out.tag) == "output"}
    return set()


def _brt_results(process: ET.Element) -> dict[str, str]:
    """resultVariable -> decisionRef dos businessRuleTask do processo."""
    return {
        brt.get(f"{_CAMUNDA}resultVariable", ""): brt.get(f"{_CAMUNDA}decisionRef", "")
        for brt in process.iter(f"{_BPMN_NS}businessRuleTask")
    }


# --- C1 ------------------------------------------------------------------------------------------


def test_prazos_do_reembolso_sao_timer_bpmn_parametrizado_por_dmn() -> None:
    process = _process()
    results = _brt_results(process)
    timers = _timers(process)
    assert len(timers) >= 3, "controle: a varredura tem de ver os timers do processo"
    parametrizados = 0
    for _event, definition in timers:
        expression = _timer_expression(definition)
        timer_id = definition.get("id", "")
        if timer_id in _TIMERS_LITERAIS_CONHECIDOS:
            assert expression == _TIMERS_LITERAIS_CONHECIDOS[timer_id][0], (
                f"{timer_id}: dívida mudou de valor"
            )
            continue
        match = _EXPRESSAO_DMN.match(expression)
        assert match, f"{timer_id}: duração '{expression}' não vem de DMN (prazo escondido no BPMN)"
        variable, field = match.groups()
        assert variable in results, f"{timer_id}: '{variable}' não é resultVariable de BRT deste processo"
        decision = results[variable]
        assert field in _dmn_outputs(decision), f"{timer_id}: '{field}' não é saída da DMN '{decision}'"
        parametrizados += 1
    assert parametrizados >= 2  # controle: BT_AlertaSla e BT_SlaAnalise


def test_a_divida_de_timer_literal_ainda_existe_e_e_literal() -> None:
    """A allowlist do C1 só encolhe: entrada que não é mais literal (ou sumiu) tem de sair dela."""
    by_id = {definition.get("id", ""): definition for _event, definition in _timers(_process())}
    for timer_id in _TIMERS_LITERAIS_CONHECIDOS:
        assert timer_id in by_id, f"{timer_id} sumiu do BPMN: tire-o de _TIMERS_LITERAIS_CONHECIDOS"
        assert _timer_expression(by_id[timer_id]) == _TIMERS_LITERAIS_CONHECIDOS[timer_id][0], timer_id


def test_os_brts_que_parametrizam_timers_apontam_para_dmn_versionada() -> None:
    for variable, decision in _brt_results(_process()).items():
        assert variable and decision
        assert _dmn_outputs(decision), f"BRT com decisionRef '{decision}' sem DMN em spec/processes/dmn"


# --- C2 ------------------------------------------------------------------------------------------


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def _arquivos_do_reembolso(*suffixes: str) -> list[Path]:
    """Arquivos de `src/` com 'reembols' no caminho RELATIVO (nunca no caminho do checkout)."""
    return sorted(
        p
        for p in (_ROOT / "src").rglob("*")
        if p.suffix in suffixes and "reembols" in _rel(p).lower() and "node_modules" not in p.parts
    )


def _nome_chamado(call: ast.Call) -> str:
    return getattr(call.func, "id", getattr(call.func, "attr", ""))


def _duracoes_em_python(path: Path) -> list[tuple[str, str]]:
    """(símbolo, trecho) de cada `timedelta`/`relativedelta`/`Timedelta`, de cada literal de duração
    ISO e de cada constante inteira com nome de prazo (`PRAZO`, `DIAS`, `SLA`, ...) no arquivo."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    nomeados: set[int] = set()
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        value = getattr(node, "value", None) if targets else None
        if value is None:
            continue
        for sub in ast.walk(value):
            if isinstance(sub, ast.Call) and _nome_chamado(sub) in {
                "timedelta",
                "relativedelta",
                "Timedelta",
            }:
                found.extend((t, ast.unparse(sub)) for t in targets)
                nomeados.add(id(sub))
        numero = value.operand if isinstance(value, ast.UnaryOp) else value
        if isinstance(numero, ast.Constant) and type(numero.value) is int:
            found.extend((t, ast.unparse(value)) for t in targets if _NOME_DE_PRAZO.search(t))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _nome_chamado(node) in {"timedelta", "relativedelta", "Timedelta"}
            and id(node) not in nomeados
        ):
            found.append(("<sem-nome>", ast.unparse(node)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ISO_DURACAO.match(node.value):
            found.append(("<literal>", node.value))
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    """Docstrings (de módulo, classe ou função) descrevem o código; não são texto que sai."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _blocos_de_texto(path: Path) -> list[str]:
    """Blocos de texto inteiros: cada string de um `.py` que não é docstring (literais adjacentes já vêm
    unidos) e cada parágrafo (linhas seguidas até uma linha em branco) dos demais arquivos. Frase
    quebrada em várias linhas fica no mesmo bloco."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(source)
        docs = _docstrings(tree)
        return [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
        ]
    return [b for b in re.split(r"\n\s*\n", source) if b.strip()]


#: Num texto de agente, um tempo conta como prazo de reembolso se aparece a até esta distância (em
#: caracteres) de "reembols", no mesmo bloco: pega a frase seguinte de um texto em várias linhas sem
#: confundir um prompt longo, que fala de reembolso e de sintomas "há 3 meses" em regras distantes.
_PROXIMIDADE = 160


def _prazo_perto_de_reembolso(bloco: str) -> list[str]:
    lowered = bloco.lower()
    posicoes = [m.start() for m in re.finditer("reembols", lowered)]
    return [
        m.group(0)
        for m in _DIAS_EM_TEXTO.finditer(bloco)
        if any(abs(m.start() - pos) <= _PROXIMIDADE for pos in posicoes)
    ]


def test_nenhum_prazo_de_reembolso_em_python_ou_prompt() -> None:
    arquivos = _arquivos_do_reembolso(".py")
    assert len(arquivos) >= 5, "controle: a varredura tem de achar os módulos do reembolso"
    conhecidos = {**_DURACOES_PYTHON_CONHECIDAS, **_INTEIROS_PYTHON_CONHECIDOS}
    vistos: set[tuple[str, str]] = set()
    for path in arquivos:
        for symbol, snippet in _duracoes_em_python(path):
            key = (_rel(path), symbol)
            assert key in conhecidos, f"prazo em Python no reembolso: {key[0]} {symbol} = {snippet}"
            vistos.add(key)
    # As allowlists só encolhem: entrada que não existe mais sai delas.
    assert vistos == set(conhecidos)


def test_nenhum_texto_ao_beneficiario_ou_de_agente_promete_prazo_de_reembolso() -> None:
    # Arquivos do reembolso (textos do Lucas em `domain/reembolso`, portal, webhook): nenhum bloco
    # conta tempo.
    do_reembolso = _arquivos_do_reembolso(".py", ".ts", ".tsx")
    assert any(_rel(p).endswith("domain/reembolso/pedido.py") for p in do_reembolso)
    for path in do_reembolso:
        for bloco in _blocos_de_texto(path):
            achado = _DIAS_EM_TEXTO.search(bloco)
            assert not achado, (
                f"prazo em texto do reembolso: {_rel(path)}: {achado.group(0)!r} em {bloco[:120]!r}"
            )
    # Texto de agente (prompts, textos, skills): bloco que fala de reembolso não conta tempo.
    textos = sorted((_ROOT / "src/maezo/agents").rglob("*.py"))
    textos += sorted(
        p
        for p in (_ROOT / "spec/agents").rglob("*")
        if p.is_file() and p.suffix in {".md", ".yaml", ".yml", ".txt"}
    )
    assert len(textos) >= 10, "controle: a varredura tem de achar texto de agente"
    falam = 0
    for path in textos:
        for bloco in _blocos_de_texto(path):
            falam += "reembols" in bloco.lower()
            achados = _prazo_perto_de_reembolso(bloco)
            assert not achados, f"prazo de reembolso em texto de agente: {_rel(path)}: {achados}"
    assert falam >= 3, "controle: há texto de agente que fala de reembolso"


def test_os_detectores_de_prazo_nao_sao_vacuos(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from datetime import timedelta\n"
        "PRAZO = timedelta(days=30)\nISO = 'P30D'\nf(timedelta(hours=1))\nOUTRO = timedelta(days=30)\n"
        "g(timedelta(days=30))\nPRAZO_PAGAMENTO_DIAS: int = 30\nLIMITE = 30\n"
        "TEXTO = ('O financeiro paga em ate '\n         '30 (trinta) dias.')\n",
        encoding="utf-8",
    )
    found = _duracoes_em_python(probe)
    assert ("PRAZO", "timedelta(days=30)") in found
    assert ("<literal>", "P30D") in found
    assert ("<sem-nome>", "timedelta(hours=1)") in found
    assert ("<sem-nome>", "timedelta(days=30)") in found  # sem nome, mesmo igual a um nomeado
    assert ("PRAZO_PAGAMENTO_DIAS", "30") in found
    assert not [f for f in found if f[0] == "LIMITE"]
    assert [b for b in _blocos_de_texto(probe) if _DIAS_EM_TEXTO.search(b)]
    yaml_probe = tmp_path / "textos.yaml"
    yaml_probe.write_text(
        "aprovado: >-\n  Seu reembolso foi aprovado.\n  O pagamento sai em 30 dias.\n", encoding="utf-8"
    )
    assert [b for b in _blocos_de_texto(yaml_probe) if _prazo_perto_de_reembolso(b)]
    assert not _prazo_perto_de_reembolso("reembolso " + "x" * (_PROXIMIDADE + 5) + " há 3 meses")
    for frase in (
        "o reembolso sai em 30 dias",
        "em trinta dias",
        "em 30 (trinta) dias",
        "em 48 horas",
        "em 1 mês",
    ):
        assert _DIAS_EM_TEXTO.search(frase), frase


# --- C3 ------------------------------------------------------------------------------------------


#: C3 — o que um disparo de timer pode alcançar antes de uma tarefa humana: (tópico, event_topic).
_PERMITIDO_APOS_TIMER = frozenset(
    {
        ("operadora.reembolso.notify_sla_risk", None),
        ("operadora.events.publish", "agents.events.reembolso.sla_breached"),
    }
)
_ESTRUTURAIS = frozenset(
    {
        "exclusiveGateway",
        "eventBasedGateway",
        "parallelGateway",
        "inclusiveGateway",
        "businessRuleTask",
        "boundaryEvent",
        "intermediateCatchEvent",
        "startEvent",
        "endEvent",
        "subProcess",
    }
)
_FIM_DE_DESFECHO = re.compile(r"Aprovad|Parcial|Negad|Cancelad|Pag")


def _parents(process: ET.Element) -> dict[str, ET.Element]:
    return {child.get("id", ""): parent for parent in process.iter() for child in parent if child.get("id")}


def _outgoing(process: ET.Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for flow in process.iter(f"{_BPMN_NS}sequenceFlow"):
        out.setdefault(flow.get("sourceRef", ""), []).append(flow.get("targetRef", ""))
    return out


def _boundaries(process: ET.Element) -> dict[str, list[str]]:
    """atividade -> boundary events presos a ela."""
    attached: dict[str, list[str]] = {}
    for event in process.iter(f"{_BPMN_NS}boundaryEvent"):
        attached.setdefault(event.get("attachedToRef", ""), []).append(event.get("id", ""))
    return attached


def _event_topic(task: ET.Element) -> str | None:
    """O `event_topic` declarado na PRÓPRIA tarefa (nunca o de uma filha de subprocesso)."""
    for param in task.findall("bpmn:extensionElements/camunda:inputOutput/camunda:inputParameter", _NS):
        if param.get("name") == "event_topic":
            return (param.text or "").strip()
    return None


def _proibido(node: ET.Element) -> str | None:
    kind, node_id = _local(node.tag), node.get("id", "")
    if kind == "userTask":
        return None
    if kind in {"endEvent", "intermediateThrowEvent"}:
        if kind == "endEvent" and _FIM_DE_DESFECHO.search(node_id):
            return "fim de desfecho"
        # Fim ou lançamento com definição que AGE (mensagem externa, sinal, escalonamento): mesma
        # allowlist das tarefas. Só o fim simples e o `terminate` são estruturais.
        for definicao in node:
            if not _local(definicao.tag).endswith("EventDefinition"):
                continue
            if _local(definicao.tag) == "terminateEventDefinition":
                continue
            chave_def = (definicao.get(f"{_CAMUNDA}topic"), _event_topic(node))
            if chave_def not in _PERMITIDO_APOS_TIMER:
                return f"{kind} com {_local(definicao.tag)} {chave_def} fora de _PERMITIDO_APOS_TIMER"
        return None
    if kind in _ESTRUTURAIS:
        return None
    chave = (node.get(f"{_CAMUNDA}topic"), _event_topic(node))
    if kind == "serviceTask" and chave in _PERMITIDO_APOS_TIMER:
        return None
    return f"{kind} {chave} fora de _PERMITIDO_APOS_TIMER"


def _ancestrais(parents: dict[str, ET.Element], node_id: str) -> set[str]:
    """Subprocessos que contêm `node_id`, do mais interno ao mais externo."""
    found: set[str] = set()
    parent = parents.get(node_id)
    while parent is not None and _local(parent.tag) == "subProcess":
        found.add(parent.get("id", ""))
        parent = parents.get(parent.get("id", ""))
    return found


def _alcance_sem_humano(process: ET.Element, start: str) -> list[str]:
    """Nós alcançados a partir de `start` até uma tarefa humana. Entra num subprocesso pelo início dele
    e, ao chegar a um fim interno, segue pela saída (e pelos boundaries) do subprocesso. Exceção: o
    token de um boundary NÃO-interruptivo que chega a um fim dentro de um subprocesso que já contém a
    atividade dele não conclui esse subprocesso (a atividade humana segue aberta); ali a busca para."""
    nodes, outgoing, attached, parents = (
        _nodes(process),
        _outgoing(process),
        _boundaries(process),
        _parents(process),
    )
    origem = nodes.get(start)
    nao_interruptivo = (
        origem is not None
        and _local(origem.tag) == "boundaryEvent"
        and origem.get("cancelActivity") == "false"
    )
    escopos_vivos = _ancestrais(parents, start) if nao_interruptivo else set()
    seen: set[str] = set()
    queue = deque(outgoing.get(start, []))
    visited: list[str] = []
    while queue:
        node_id = queue.popleft()
        if node_id in seen or node_id not in nodes:
            continue
        seen.add(node_id)
        visited.append(node_id)
        node = nodes[node_id]
        kind = _local(node.tag)
        if kind == "userTask":
            continue  # a partir daqui decide um humano
        if kind == "subProcess":
            queue.extend(c.get("id", "") for c in node if _local(c.tag) == "startEvent")
        parent = parents.get(node_id)
        if (
            kind == "endEvent"
            and parent is not None
            and _local(parent.tag) == "subProcess"
            and (
                parent.get("id", "") not in escopos_vivos
                # `terminate` encerra o escopo inteiro (e a tarefa humana dele): o subprocesso conclui.
                or any(_local(c.tag) == "terminateEventDefinition" for c in node)
            )
        ):
            queue.extend(outgoing.get(parent.get("id", ""), []))
            queue.extend(attached.get(parent.get("id", ""), []))
        queue.extend(outgoing.get(node_id, []))
        queue.extend(attached.get(node_id, []))  # boundary de erro de um serviço também é caminho
    return visited


def _violacoes(process: ET.Element) -> list[str]:
    nodes = _nodes(process)
    erros = []
    for event, _definition in _timers(process):
        for node_id in _alcance_sem_humano(process, event.get("id", "")):
            motivo = _proibido(nodes[node_id])
            if motivo:
                erros.append(f"timer {event.get('id')} alcança {node_id} sem humano: {motivo}")
    return erros


def test_vencimento_de_timer_do_reembolso_nunca_paga_nem_aprova() -> None:
    process = _process()
    nodes = _nodes(process)
    timers = _timers(process)
    assert len(timers) >= 3
    assert not _violacoes(process)
    humanos = 0
    for event, _definition in timers:
        alcance = _alcance_sem_humano(process, event.get("id", ""))
        assert alcance, f"controle: o timer {event.get('id')} tem saída"
        humanos += any(_local(nodes[n].tag) == "userTask" for n in alcance)
    assert humanos >= 2  # BT_SlaAnalise -> coordenação; ICE_PrazoPendencia -> decisão humana


def test_o_detector_de_caminho_proibido_nao_e_vacuo() -> None:
    process = _process()
    nodes = _nodes(process)
    # Controle: o mesmo detector, partindo da decisão humana, acha o que proíbe para os timers.
    proibidos = {n for n in _alcance_sem_humano(process, "GW_DecisaoReembolso") if _proibido(nodes[n])}
    assert proibidos, "o detector não reconhece nenhum desfecho alcançável pela decisão humana"


_SINTETICO = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:camunda="http://camunda.org/schema/1.0/bpmn" id="d">
  <bpmn:process id="p">
    {corpo}
  </bpmn:process>
</bpmn:definitions>"""

_TIMER = (
    '<bpmn:intermediateCatchEvent id="T"><bpmn:timerEventDefinition id="TD"/></bpmn:intermediateCatchEvent>'
)
_NEGA = 'camunda:type="external" camunda:topic="operadora.reembolso.send_reembolso_denial"'


@pytest.mark.parametrize(
    "corpo",
    [
        # timer -> subprocesso cujo interior nega
        f"""{_TIMER}
        <bpmn:subProcess id="SP"><bpmn:startEvent id="S"/><bpmn:serviceTask id="N" {_NEGA}/>
          <bpmn:endEvent id="E"/><bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="N"/>
          <bpmn:sequenceFlow id="f2" sourceRef="N" targetRef="E"/></bpmn:subProcess>
        <bpmn:sequenceFlow id="f0" sourceRef="T" targetRef="SP"/>""",
        # timer dentro de subprocesso cuja saída nega
        f"""<bpmn:subProcess id="SP"><bpmn:startEvent id="S"/>
          {_TIMER}
          <bpmn:endEvent id="E"/><bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="T"/>
          <bpmn:sequenceFlow id="f2" sourceRef="T" targetRef="E"/></bpmn:subProcess>
        <bpmn:serviceTask id="N" {_NEGA}/><bpmn:sequenceFlow id="f3" sourceRef="SP" targetRef="N"/>""",
        # timer -> fim que manda mensagem externa de negativa
        f"""{_TIMER}
        <bpmn:endEvent id="E"><bpmn:messageEventDefinition id="M" {_NEGA}/></bpmn:endEvent>
        <bpmn:sequenceFlow id="f0" sourceRef="T" targetRef="E"/>""",
        # timer -> tarefa de envio desconhecida (fora da allowlist)
        f"""{_TIMER}
        <bpmn:sendTask id="X"/><bpmn:sequenceFlow id="f0" sourceRef="T" targetRef="X"/>""",
    ],
)
def test_o_detector_ve_dentro_e_fora_de_subprocesso(corpo: str) -> None:
    process = ET.fromstring(_SINTETICO.format(corpo=corpo)).find("bpmn:process", _NS)
    assert process is not None
    assert _violacoes(process)


def test_alerta_nao_interruptivo_que_termina_dentro_do_subprocesso_nao_sai_dele() -> None:
    """O fim do ramo do alerta não conclui o subprocesso: a tarefa humana segue aberta. A saída do
    subprocesso (que só leva a uma negativa depois da decisão) não é alcançada pelo alerta."""
    corpo = f"""<bpmn:subProcess id="SP"><bpmn:startEvent id="S"/><bpmn:userTask id="U"/>
      <bpmn:boundaryEvent id="B" cancelActivity="false" attachedToRef="U">
        <bpmn:timerEventDefinition id="TB"/></bpmn:boundaryEvent>
      <bpmn:endEvent id="EA"/><bpmn:endEvent id="EU"/>
      <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="U"/>
      <bpmn:sequenceFlow id="f2" sourceRef="B" targetRef="EA"/>
      <bpmn:sequenceFlow id="f3" sourceRef="U" targetRef="EU"/></bpmn:subProcess>
    <bpmn:serviceTask id="N" {_NEGA}/><bpmn:sequenceFlow id="f4" sourceRef="SP" targetRef="N"/>"""
    process = ET.fromstring(_SINTETICO.format(corpo=corpo)).find("bpmn:process", _NS)
    assert process is not None
    assert not _violacoes(process)
    # Controle: o mesmo desenho com o alerta INTERRUPTIVO sai do subprocesso e alcança a negativa.
    corpo_interruptivo = corpo.replace(' cancelActivity="false"', "")
    interruptivo = ET.fromstring(_SINTETICO.format(corpo=corpo_interruptivo)).find("bpmn:process", _NS)
    assert interruptivo is not None
    assert _violacoes(interruptivo)


def test_alerta_nao_interruptivo_que_termina_com_terminate_sai_do_subprocesso() -> None:
    """Um `terminate` no ramo do alerta cancela a tarefa humana e conclui o subprocesso: a saída
    (que leva a uma negativa) passa a ser alcançada sem humano, e a cerca acusa."""
    corpo = f"""<bpmn:subProcess id="SP"><bpmn:startEvent id="S"/><bpmn:userTask id="U"/>
      <bpmn:boundaryEvent id="B" cancelActivity="false" attachedToRef="U">
        <bpmn:timerEventDefinition id="TB"/></bpmn:boundaryEvent>
      <bpmn:endEvent id="EA"><bpmn:terminateEventDefinition id="TT"/></bpmn:endEvent><bpmn:endEvent id="EU"/>
      <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="U"/>
      <bpmn:sequenceFlow id="f2" sourceRef="B" targetRef="EA"/>
      <bpmn:sequenceFlow id="f3" sourceRef="U" targetRef="EU"/></bpmn:subProcess>
    <bpmn:serviceTask id="N" {_NEGA}/><bpmn:sequenceFlow id="f4" sourceRef="SP" targetRef="N"/>"""
    process = ET.fromstring(_SINTETICO.format(corpo=corpo)).find("bpmn:process", _NS)
    assert process is not None
    assert _violacoes(process)
