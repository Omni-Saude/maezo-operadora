"""R-027 — SP-OP-INADIMPLENCIA-001 sob a RN 593/2023 alterada pela RN 617/2024 (F-001, F-038).

Direcao decidida pelo dono (issue #1081, DEC-10 -> R-019), NAO re-decidida aqui:

* notificacao ate o 50o dia de inadimplencia e PRE-REQUISITO, modelada como TIMER BPMN dirigido
  pela DMN (`${purga.prazo_notificacao_previa}`);
* suspensao/exclusao so 10 dias APOS a notificacao; notificacao tardia continua valida se ainda
  garantir os 10 dias;
* minimo de 2 mensalidades nao pagas — a RN 617 retirou a janela de 12 meses (corrige F-001);
* o worker orfao `calculate_purge` sai: o prazo vive SO na DMN `inadimplencia_purga`;
* guard adverso da suspensao exige tier de alcada e antecedencia (F-038).

Cada familia cobre plano individual/familiar E coletivo (lacuna do repro F-001, que so olhava
`individual`). Os nomes seguem o seletor do DoD do item:
`-k "inadimplencia and (purge or notificacao_50 or dois_meses)"`.

Fixtures sinteticas — nenhum dado real. A spec e lida ESTATICAMENTE (o teste le o artefato); a
avaliacao de DMN em runtime continua so no engine (ADR-0028) — aqui entra por `FakeDmnTransport`
carregado com a linha que a propria DMN da arvore publica.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers import inadimplencia as inad
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import WorkerBpmnError

_REPO = Path(__file__).resolve().parents[4]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn"
_DMN_PURGA = _REPO / "spec/processes/dmn/inadimplencia_purga.dmn"
_DMN_STATUS = _REPO / "spec/processes/dmn/inadimplencia_status.dmn"

_DMN = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"
_BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_TOPIC = "{http://camunda.org/schema/1.0/bpmn}topic"

_INDIVIDUAIS = ("individual", "familiar")
_COLETIVOS = ("coletivo_empresarial", "coletivo_adesao")
_TODOS_OS_PLANOS = _INDIVIDUAIS + _COLETIVOS

_HOJE = dt.date(2026, 10, 29)
_TOPICO_SUSPENSAO = "operadora.inadimplencia.register_contract_suspension"


# ---------------------------------------------------------------------------
# Leitura estatica dos artefatos
# ---------------------------------------------------------------------------


def _xml(path: Path) -> ET.Element:
    return parse_xml_without_dtd(path.read_bytes())


def _text(el: ET.Element | None) -> str:
    assert el is not None
    return (el.text or "").strip()


def _dmn_rows(path: Path) -> list[dict[str, Any]]:
    """Linhas da decisionTable: `inputs` por expressao de entrada, `outputs` por nome de saida."""
    table = _xml(path).find(f".//{_DMN}decisionTable")
    assert table is not None
    input_names = [_text(i.find(f"{_DMN}inputExpression/{_DMN}text")) for i in table.findall(f"{_DMN}input")]
    output_names = [o.get("name", "") for o in table.findall(f"{_DMN}output")]
    rows = []
    for rule in table.findall(f"{_DMN}rule"):
        ins = [_text(e.find(f"{_DMN}text")) for e in rule.findall(f"{_DMN}inputEntry")]
        outs = [_text(e.find(f"{_DMN}text")) for e in rule.findall(f"{_DMN}outputEntry")]
        rows.append(
            {
                "id": rule.get("id"),
                "inputs": dict(zip(input_names, ins, strict=True)),
                "outputs": dict(zip(output_names, outs, strict=True)),
            }
        )
    return rows


def _purga_row_for(tipo_plano: str) -> dict[str, str]:
    """FIRST-hit da `inadimplencia_purga` (entrada unica `tipo_plano`: lista de literais ou `-`)."""
    for row in _dmn_rows(_DMN_PURGA):
        entry = row["inputs"]["tipo_plano"]
        if entry == "-" or f'"{tipo_plano}"' in entry.split(","):
            return {k: v.strip('"') for k, v in row["outputs"].items()}
    raise AssertionError(f"nenhuma linha da inadimplencia_purga casou {tipo_plano!r}")


def _dias(iso: str) -> int:
    match = re.fullmatch(r"P(\d+)D", iso)
    assert match, f"prazo fora da forma P<n>D: {iso!r}"
    return int(match.group(1))


def _process() -> ET.Element:
    proc = _xml(_BPMN).find(f"{_BPMN_NS}process")
    assert proc is not None
    return proc


def _by_id(proc: ET.Element) -> dict[str, ET.Element]:
    return {e.get("id", ""): e for e in proc.iter() if e.get("id")}


def _flows(proc: ET.Element) -> list[ET.Element]:
    return proc.findall(f"{_BPMN_NS}sequenceFlow")


def _timer_da_notificacao(proc: ET.Element) -> list[ET.Element]:
    return [
        e
        for e in proc.iter(f"{_BPMN_NS}intermediateCatchEvent")
        if (td := e.find(f"{_BPMN_NS}timerEventDefinition/{_BPMN_NS}timeDuration")) is not None
        and (td.text or "").strip() == "${purga.prazo_notificacao_previa}"
    ]


def _purga_fake(tipo_plano: str) -> FakeDmnTransport:
    """O fake devolve EXATAMENTE a linha que a DMN da arvore produz para o plano (sem copia manual)."""
    fake = FakeDmnTransport()
    fake.register("inadimplencia_purga", [_purga_row_for(tipo_plano)])
    return fake


def _suspensao_estrita(tipo_plano: str, **overrides: Any) -> dict[str, Any]:
    """Baseline que PASSA no guard estrito — a falha observada vem so do campo sob teste."""
    notificacao = _HOJE - dt.timedelta(days=3)
    efeito = notificacao + dt.timedelta(days=_dias(_purga_row_for(tipo_plano)["prazo_purga"]))
    base: dict[str, Any] = {
        "decisao_inadimplencia": inad.DECISAO_SUSPENDER,
        "responsavel_id": "juridico-sintetico-001",
        "tier": "tier-2",
        "fundamentacao_contratual": "clausula sintetica de suspensao",
        "referencia_regulatoria": "RN 593/2023 alterada pela RN 617/2024",
        "comprovacao_notificacao_previa": "ref-comprovante-sintetico-0001",
        "comprovacao_periodo_minimo": "ref-periodo-sintetico-0001",
        "data_notificacao_iso": notificacao.isoformat(),
        "data_efeito_iso": efeito.isoformat(),
        "ja_em_rescisao_cancel": False,
        "numero_contrato": "C-SINT-0001",
        "tipo_plano": tipo_plano,
    }
    base.update(overrides)
    return base


def _suspender(variables: dict[str, Any], tipo_plano: str) -> dict[str, Any]:
    return inad.register_contract_suspension(variables, dmn=_purga_fake(tipo_plano), hoje=_HOJE)


class _RecordingHarness:
    def __init__(self) -> None:
        self.workers: dict[str, Any] = {}
        self.handlers: dict[str, Any] = {}

    def register_worker(self, worker: Any) -> None:
        self.workers[worker.topic] = worker

    def register(self, topic: str, handler: Any, *, variables: list[str] | None = None) -> None:
        del variables
        self.handlers[topic] = handler


def _registered(dmn: FakeDmnTransport | None = None, **seams: Any) -> _RecordingHarness:
    harness = _RecordingHarness()
    inad.register_inadimplencia_workers(
        harness,  # type: ignore[arg-type]
        None,
        dmn=dmn or FakeDmnTransport(),
        engine=FakeCibSevenTransport(),
        **seams,
    )
    return harness


# ---------------------------------------------------------------------------
# purge — o prazo de purga vive SO na DMN; o worker orfao sai (F-001)
# ---------------------------------------------------------------------------


def test_inadimplencia_purge_worker_orfao_calculate_purge_removido() -> None:
    """Nenhuma copia Python do prazo regulatorio: a funcao saiu do modulo."""
    assert not hasattr(inad, "calculate_purge")
    src = Path(inad.__file__).read_text(encoding="utf-8")
    for literal in ('"P10D"', '"P15D"', '"P30D"', '"P50D"', '"P60D"', '"P90D"'):
        assert literal not in src


@pytest.mark.parametrize("estrito", [False, True])
def test_inadimplencia_purge_topico_orfao_nao_registrado(estrito: bool) -> None:
    harness = _registered(inad_rn593_estrito=estrito)
    topics = set(harness.workers) | set(harness.handlers)
    assert "operadora.inadimplencia.calculate_purge" not in topics
    bpmn_topics = {e.get(_CAMUNDA_TOPIC) for e in _process().iter(f"{_BPMN_NS}serviceTask")}
    # Todo topico registrado e consumido pelo BPMN — exceto assess_status (DMN via seam, legado).
    assert topics - bpmn_topics == {"operadora.inadimplencia.assess_status"}


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_purge_dmn_garante_ao_menos_10_dias(tipo_plano: str) -> None:
    """Exclusao/suspensao so 10 dias apos a notificacao (RN 593 alterada pela RN 617).

    Individual/familiar = exatamente P10D; coletivo mantem o prazo conservador (maior) ja
    publicado na DMN — nunca menos que 10 dias.
    """
    prazo = _dias(_purga_row_for(tipo_plano)["prazo_purga"])
    if tipo_plano in _INDIVIDUAIS:
        assert prazo == 10
    else:
        assert prazo >= 10


def test_inadimplencia_purge_catchall_nunca_abaixo_de_10_dias() -> None:
    catchall = _dmn_rows(_DMN_PURGA)[-1]
    assert catchall["inputs"]["tipo_plano"] == "-"
    assert _dias(catchall["outputs"]["prazo_purga"].strip('"')) >= 10


# ---------------------------------------------------------------------------
# notificacao_50 — pre-requisito com timer BPMN + guard com tier e antecedencia (F-001, F-038)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_dmn_prazo_ate_o_50o_dia(tipo_plano: str) -> None:
    """O prazo da notificacao e o TETO do 50o dia — nao mais um prazo relativo de 10/15 dias."""
    assert _purga_row_for(tipo_plano)["prazo_notificacao_previa"] == "P50D"


def test_inadimplencia_notificacao_50_catchall_tambem_50_dias() -> None:
    catchall = _dmn_rows(_DMN_PURGA)[-1]
    assert catchall["outputs"]["prazo_notificacao_previa"] == '"P50D"'


def test_inadimplencia_notificacao_50_timer_bpmn_dirigido_pela_dmn() -> None:
    """O prazo do 50o dia e um TIMER BPMN (prazo regulatorio -> timer) lido da DMN."""
    proc = _process()
    ids = _by_id(proc)
    flows = _flows(proc)
    (timer,) = _timer_da_notificacao(proc)
    (entrada,) = [f for f in flows if f.get("targetRef") == timer.get("id")]
    gateway = ids[entrada.get("sourceRef", "")]
    assert gateway.tag == f"{_BPMN_NS}eventBasedGateway"
    # A corrida do pre-requisito: pagamento (purga) x confirmacao da notificacao x 50o dia.
    rivais = [
        ids[f.get("targetRef", "")]
        for f in flows
        if f.get("sourceRef") == gateway.get("id") and f.get("targetRef") != timer.get("id")
    ]
    refs = {
        d.get("messageRef")
        for d in (r.find(f"{_BPMN_NS}messageEventDefinition") for r in rivais)
        if d is not None
    }
    assert refs == {"Msg_InadPagamentoRecebido", "Msg_InadNotificacaoAck"}
    assert len(rivais) == 2


def test_inadimplencia_notificacao_50_expiracao_vai_a_humano_nunca_adverso() -> None:
    """Expirado o 50o dia sem notificacao comprovada: analise HUMANA, nunca um terminal adverso."""
    proc = _process()
    ids = _by_id(proc)
    out: dict[str, list[str]] = {}
    for f in _flows(proc):
        out.setdefault(f.get("sourceRef", ""), []).append(f.get("targetRef", ""))
    (timer,) = _timer_da_notificacao(proc)
    seen: set[str] = set()
    frontier = [timer.get("id", "")]
    user_tasks: set[str] = set()
    while frontier:
        node = frontier.pop()
        if node in seen:
            continue
        seen.add(node)
        if ids[node].tag == f"{_BPMN_NS}userTask":
            user_tasks.add(node)
            continue
        assert node not in {"ST_RegisterSuspension", "End_ContratoSuspenso_Inad"}
        frontier.extend(out.get(node, []))
    assert user_tasks == {"UT_AnaliseInadimplencia"}


def test_inadimplencia_notificacao_50_gate_dark_e_default_legado() -> None:
    """Flag OFF (padrao) => o default do gate leva ao cure-window legado (comportamento de hoje)."""
    proc = _process()
    ids = _by_id(proc)
    gate = ids["GW_NotificacaoPrerequisito"]
    assert gate.tag == f"{_BPMN_NS}exclusiveGateway"
    default = ids[gate.get("default", "")]
    assert default.get("targetRef") == "GW_CureWindow"
    (cond_flow,) = [
        f for f in _flows(proc) if f.get("sourceRef") == "GW_NotificacaoPrerequisito" and f is not default
    ]
    cond = _text(cond_flow.find(f"{_BPMN_NS}conditionExpression"))
    assert "rn593_prerequisito_notificacao" in cond
    assert "notificacao_previa_feita" in cond


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
@pytest.mark.parametrize("estrito", [False, True])
def test_inadimplencia_notificacao_50_resolve_facts_publica_a_flag(tipo_plano: str, estrito: bool) -> None:
    out = inad.resolve_facts(
        {
            "tipo_plano": tipo_plano,
            "competencias_em_aberto": ["2026-08", "2026-09"],
            "numero_contrato": "C-1",
            "tenant_id": "t",
        },
        engine=FakeCibSevenTransport(),
        rn593_estrito=estrito,
    )
    assert out["rn593_prerequisito_notificacao"] is estrito


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_suspensao_estrita_passa(tipo_plano: str) -> None:
    out = _suspender(_suspensao_estrita(tipo_plano), tipo_plano)
    assert out["suspensao_registrada"] is True


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_suspensao_so_apos_prazo_da_notificacao(tipo_plano: str) -> None:
    """Um dia antes de `notificacao + prazo_purga` (DMN) => recusa; no dia exato => passa."""
    prazo = _dias(_purga_row_for(tipo_plano)["prazo_purga"])
    notificacao = (_HOJE - dt.timedelta(days=1)).isoformat()
    cedo = (_HOJE - dt.timedelta(days=1) + dt.timedelta(days=prazo - 1)).isoformat()
    with pytest.raises(WorkerBpmnError) as exc:
        _suspender(
            _suspensao_estrita(tipo_plano, data_notificacao_iso=notificacao, data_efeito_iso=cedo), tipo_plano
        )
    assert exc.value.error_code == inad.ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "antecedencia" in str(exc.value)
    no_prazo = (_HOJE - dt.timedelta(days=1) + dt.timedelta(days=prazo)).isoformat()
    ok = _suspender(
        _suspensao_estrita(tipo_plano, data_notificacao_iso=notificacao, data_efeito_iso=no_prazo), tipo_plano
    )
    assert ok["suspensao_registrada"] is True


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_notificacao_tardia_valida_se_garante_o_prazo(tipo_plano: str) -> None:
    """RN 617: notificacao feita depois do 50o dia vale se ainda garantir o prazo apos ela."""
    prazo = _dias(_purga_row_for(tipo_plano)["prazo_purga"])
    efeito = (_HOJE + dt.timedelta(days=prazo)).isoformat()
    out = _suspender(
        _suspensao_estrita(tipo_plano, data_notificacao_iso=_HOJE.isoformat(), data_efeito_iso=efeito),
        tipo_plano,
    )
    assert out["suspensao_registrada"] is True


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
@pytest.mark.parametrize("tier", ["", "   ", None, 2])
def test_inadimplencia_notificacao_50_suspensao_recusa_sem_tier(tipo_plano: str, tier: Any) -> None:
    """F-038: guard adverso exige tier de alcada (ausente/branco/nao-string => recusa)."""
    with pytest.raises(WorkerBpmnError) as exc:
        _suspender(_suspensao_estrita(tipo_plano, tier=tier), tipo_plano)
    assert "tier" in str(exc.value)


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_suspensao_recusa_efeito_retroativo(tipo_plano: str) -> None:
    """F-038: data de efeito no passado nunca passa (o repro usa 1999-01-01)."""
    with pytest.raises(WorkerBpmnError) as exc:
        _suspender(
            _suspensao_estrita(tipo_plano, data_notificacao_iso="1998-12-01", data_efeito_iso="1999-01-01"),
            tipo_plano,
        )
    assert "retroativa" in str(exc.value)


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
@pytest.mark.parametrize(
    ("campo", "valor", "trecho"),
    [
        ("data_efeito_iso", "", "data_efeito_iso"),
        ("data_efeito_iso", "29/10/2026", "data_efeito_iso"),
        ("data_efeito_iso", None, "data_efeito_iso"),
        ("data_notificacao_iso", "", "data_notificacao_iso"),
        ("data_notificacao_iso", "2026-13-01", "data_notificacao_iso"),
        ("data_notificacao_iso", "2026-11-05", "futuro"),
    ],
)
def test_inadimplencia_notificacao_50_suspensao_recusa_datas_invalidas(
    tipo_plano: str, campo: str, valor: Any, trecho: str
) -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        _suspender(_suspensao_estrita(tipo_plano, **{campo: valor}), tipo_plano)
    assert trecho in str(exc.value)


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_suspensao_sem_dmn_fail_closed(tipo_plano: str) -> None:
    """Sem a DMN nao ha prazo regulatorio conhecido: recusa (nunca um prazo inventado em Python)."""
    with pytest.raises(WorkerBpmnError) as exc:
        inad.register_contract_suspension(_suspensao_estrita(tipo_plano), dmn=None, hoje=_HOJE)
    assert "inadimplencia_purga" in str(exc.value)


def test_inadimplencia_notificacao_50_suspensao_prazo_fora_da_forma_recusa() -> None:
    fake = FakeDmnTransport()
    fake.register("inadimplencia_purga", [{**_purga_row_for("individual"), "prazo_purga": "P1M"}])
    with pytest.raises(WorkerBpmnError) as exc:
        inad.register_contract_suspension(_suspensao_estrita("individual"), dmn=fake, hoje=_HOJE)
    assert "prazo_purga" in str(exc.value)


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_notificacao_50_worker_registrado_segue_a_flag(tipo_plano: str) -> None:
    """Dark: flag OFF => o worker registrado mantem o guard de hoje; ON => tier + antecedencia."""
    legado = {
        k: v for k, v in _suspensao_estrita(tipo_plano).items() if k not in {"tier", "data_notificacao_iso"}
    }
    legado["data_efeito_iso"] = "1999-01-01"
    off = _registered(dmn=_purga_fake(tipo_plano)).workers[_TOPICO_SUSPENSAO]
    assert off.execute(dict(legado))["suspensao_registrada"] is True
    on = _registered(dmn=_purga_fake(tipo_plano), inad_rn593_estrito=True).workers[_TOPICO_SUSPENSAO]
    with pytest.raises(WorkerBpmnError):
        on.execute(dict(legado))
    assert (
        on.execute(_suspensao_estrita(tipo_plano, data_efeito_iso="2099-01-01"))["suspensao_registrada"]
        is True
    )


# ---------------------------------------------------------------------------
# dois_meses — minimo de 2 mensalidades, SEM janela de 12 meses (RN 617 corrige F-001)
# ---------------------------------------------------------------------------


def _fatos(tipo_plano: str, competencias: list[Any], *, estrito: bool = True) -> dict[str, Any]:
    return inad.resolve_facts(
        {
            "tipo_plano": tipo_plano,
            "competencias_em_aberto": competencias,
            "numero_contrato": "C-1",
            "tenant_id": "t",
        },
        engine=FakeCibSevenTransport(),
        rn593_estrito=estrito,
    )


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_dois_meses_uma_mensalidade_nao_basta(tipo_plano: str) -> None:
    out = _fatos(tipo_plano, ["2026-09"])
    assert out["meses_inadimplencia"] == 1
    assert out["dentro_periodo_minimo"] is False


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_dois_meses_duas_mensalidades_bastam(tipo_plano: str) -> None:
    out = _fatos(tipo_plano, ["2026-08", "2026-09"])
    assert out["meses_inadimplencia"] == 2
    assert out["dentro_periodo_minimo"] is True


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_dois_meses_sem_janela_de_12_meses(tipo_plano: str) -> None:
    """Nao consecutivas e a mais de 12 meses de distancia ainda contam (RN 617 retirou a janela)."""
    out = _fatos(tipo_plano, ["2024-01", "2026-09"])
    assert out["dentro_periodo_minimo"] is True


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_dois_meses_competencia_repetida_conta_uma_vez(tipo_plano: str) -> None:
    """Estrito: a MESMA competencia repetida (ou lixo) nao vira 2 mensalidades."""
    out = _fatos(tipo_plano, ["2026-09", "2026-09", " 2026-09 ", "nao-e-competencia", None])
    assert out["meses_inadimplencia"] == 1
    assert out["dentro_periodo_minimo"] is False


@pytest.mark.parametrize("tipo_plano", _TODOS_OS_PLANOS)
def test_inadimplencia_dois_meses_flag_off_preserva_contagem_legada(tipo_plano: str) -> None:
    """Dark: com a flag OFF a contagem de hoje (len da lista) fica identica."""
    out = _fatos(tipo_plano, ["2026-09", "2026-09"], estrito=False)
    assert out["meses_inadimplencia"] == 2


def test_inadimplencia_dois_meses_dmn_status_exige_duas_mensalidades() -> None:
    """A regra deterministica vive na DMN: SEGUE_ANALISE exige meses_inadimplencia >= 2."""
    rows = {r["id"]: r for r in _dmn_rows(_DMN_STATUS)}
    assert rows["r_segue_analise"]["inputs"]["meses_inadimplencia"] == ">= 2"
    # Nenhuma entrada nem celula codifica uma janela de 12 meses (a RN 617 a retirou).
    for path in (_DMN_STATUS, _DMN_PURGA):
        for row in _dmn_rows(path):
            assert all("12" not in name for name in row["inputs"])
            assert all("12" not in cell for cell in row["inputs"].values())


def test_inadimplencia_dois_meses_limiar_python_espelha_a_dmn() -> None:
    """Paridade: o fato `dentro_periodo_minimo` usa o MESMO limiar que a DMN publica."""
    rows = {r["id"]: r for r in _dmn_rows(_DMN_STATUS)}
    limiar = int(rows["r_segue_analise"]["inputs"]["meses_inadimplencia"].removeprefix(">=").strip())
    assert limiar == inad.MENSALIDADES_MINIMAS_INADIMPLIDAS
