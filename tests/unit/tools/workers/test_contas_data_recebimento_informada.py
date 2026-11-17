"""DL-0174 — `data_recebimento_lote_informada`: a data de recebimento CRUA do lote (SP-OP-CONTAS-001).

A ancora `data_recebimento_lote` e defaultada para HOJE/UTC pelo intake quando o lote nao a traz (os
timers de SLA precisam dela) e, na reentrada por `BME_LinhasAtualizadas`, o valor padrao fica
indistinguivel do recebido. A variavel nova guarda a data EXATAMENTE como o lote a informou:
anulavel, nunca defaultada, ecoada na reentrada (detectada por um FATO DO PROCESSO, `has_glosas`),
`None`/`instancia_legada` numa instancia anterior a esta mudanca. A ancora nao muda.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
import structlog

from maezo.agents.marina import delegation as marina_delegation
from maezo.agents.marina import graph as marina_graph
from maezo.gateway import engine_schemas
from maezo.tools.workers import contas
from maezo.tools.workers.contas import (
    DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR,
    DATA_RECEBIMENTO_INFORMADA_VAR,
    MARCADOR_REENTRADA_INTAKE,
    MOTIVO_DATA_INFORMADA_AUSENTE,
    MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO,
    MOTIVO_DATA_INFORMADA_FUTURA,
    MOTIVO_DATA_INFORMADA_INSTANCIA_LEGADA,
    MOTIVO_DATA_INFORMADA_VALOR_ARMAZENADO_INVALIDO,
    MOTIVOS_DATA_RECEBIMENTO_INFORMADA,
    identify_glosa_entry,
)

HOJE = date(2026, 11, 17)
EVENTO = "contas.identify_glosa.data_recebimento_lote_informada_nula"
TOPICO = "operadora.contas.identify_glosa"
#: "2026-11-10" com o ano em digitos de largura total (U+FF10..U+FF19): `\d` os casaria.
_DIGITOS_UNICODE = "".join(chr(0xFF10 + int(c)) for c in "2026") + "-11-10"


@pytest.fixture(autouse=True)
def _relogio_fixo(monkeypatch: pytest.MonkeyPatch) -> None:
    """O relogio do processo fixado em `HOJE` — o mesmo que defaulta a ancora."""
    monkeypatch.setattr(contas, "_hoje_utc", lambda: HOJE)


def _variaveis(**extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-TESTE-0174",
        "linhas_conta_refs": [{"valor_apresentado_brl": 100.0}],
    }
    variables.update(extra)
    return variables


def _eventos(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == EVENTO]


# ---------------------------------------------------------------------------
# Primeira passagem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [("2026-11-10", "2026-11-10"), ("  2026-11-10\t", "2026-11-10"), ("2026-11-17", "2026-11-17")],
    ids=["iso", "iso_com_espacos_nas_pontas", "hoje"],
)
def test_primeira_passagem_data_valida_e_gravada_crua(bruto: str, esperado: str) -> None:
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(_variaveis(data_recebimento_lote=bruto))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] == esperado
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] is None
    assert out["data_recebimento_lote"] == esperado, "a ancora segue a mesma data quando ela e valida"
    assert not _eventos(logs), "data valida nao gera aviso"


@pytest.mark.parametrize(
    "variaveis_extra",
    [{}, {"data_recebimento_lote": None}, {"data_recebimento_lote": ""}, {"data_recebimento_lote": "  \t"}],
    ids=["chave_ausente", "none", "vazia", "so_espacos"],
)
def test_primeira_passagem_ausente_e_nula_com_motivo_ausente(variaveis_extra: dict[str, Any]) -> None:
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(_variaveis(**variaveis_extra))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_AUSENTE
    # A ancora continua defaultada para hoje (comportamento inalterado), e a crua nao a copia.
    assert out["data_recebimento_lote"] == HOJE.isoformat()
    [evento] = _eventos(logs)
    assert evento["motivo"] == MOTIVO_DATA_INFORMADA_AUSENTE


@pytest.mark.parametrize(
    ("bruto", "ancora_esperada"),
    [
        ("2026-1-5", "2026-1-5"),  # a ancora (inalterada) aceita via strptime
        ("2026-11-10T00:00:00", "2026-11-10"),
        ("2026-11-10T00:00:00Z", "2026-11-10"),
        ("20261110", HOJE.isoformat()),
        ("10/11/2026", HOJE.isoformat()),
        ("nao-e-data", HOJE.isoformat()),
        ("2026-02-30", HOJE.isoformat()),
        ("2026-11-10 x", HOJE.isoformat()),
        (_DIGITOS_UNICODE, _DIGITOS_UNICODE),  # idem: strptime aceita digitos Unicode
    ],
    ids=[
        "sem_zero_a_esquerda",
        "datetime",
        "datetime_utc",
        "compacta",
        "formato_brasileiro",
        "lixo",
        "data_impossivel",
        "sufixo",
        "digitos_unicode",
    ],
)
def test_primeira_passagem_formato_invalido_e_nulo(bruto: str, ancora_esperada: str) -> None:
    out = identify_glosa_entry(_variaveis(data_recebimento_lote=bruto))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO
    assert out["data_recebimento_lote"] == ancora_esperada, "a normalizacao da ancora nao mudou"


def test_primeira_passagem_valor_nao_texto_e_formato_invalido() -> None:
    out = identify_glosa_entry(_variaveis(data_recebimento_lote=20261110))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO


def test_primeira_passagem_data_futura_e_nula_e_a_ancora_nao_muda() -> None:
    out = identify_glosa_entry(_variaveis(data_recebimento_lote="2026-11-18"))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FUTURA
    assert out["data_recebimento_lote"] == "2026-11-18", "a ancora segue aceitando a data (inalterada)"


def test_o_relogio_e_o_mesmo_da_ancora(monkeypatch: pytest.MonkeyPatch) -> None:
    """Data futura e default da ancora leem o MESMO relogio: movido o relogio, os dois andam juntos."""
    monkeypatch.setattr(contas, "_hoje_utc", lambda: date(2026, 11, 18))
    valida = identify_glosa_entry(_variaveis(data_recebimento_lote="2026-11-18"))
    assert valida[DATA_RECEBIMENTO_INFORMADA_VAR] == "2026-11-18"
    sem_data = identify_glosa_entry(_variaveis())
    assert sem_data["data_recebimento_lote"] == "2026-11-18"


# ---------------------------------------------------------------------------
# Reentrada (BME_LinhasAtualizadas -> Flow_Reavalia_Apurar -> ST_ApurarDivergencias)
# ---------------------------------------------------------------------------


def test_reentrada_com_nula_continua_nula_mesmo_com_a_ancora_em_hoje() -> None:
    """A regressao central: o lote veio SEM data, a primeira passagem defaultou a ancora para hoje.
    Na reentrada a ancora e uma data valida de hoje — e a crua tem de continuar nula."""
    primeira = identify_glosa_entry(_variaveis())
    assert primeira[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    # O processo devolve ao intake TODAS as saidas da primeira passagem (a ancora ja defaultada).
    reentrada_vars = _variaveis(**primeira)
    assert reentrada_vars["data_recebimento_lote"] == HOJE.isoformat()
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(reentrada_vars)
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_AUSENTE
    assert out["data_recebimento_lote"] == HOJE.isoformat()
    assert not _eventos(logs), "o eco da reentrada nao repete o aviso da primeira passagem"


def test_reentrada_ecoa_a_data_informada_mesmo_que_a_ancora_tenha_mudado() -> None:
    out = identify_glosa_entry(
        _variaveis(
            has_glosas=True,
            data_recebimento_lote="2026-11-05",
            data_recebimento_lote_informada="2026-11-01",
            data_recebimento_lote_informada_motivo=None,
        )
    )
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] == "2026-11-01"
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] is None
    assert out["data_recebimento_lote"] == "2026-11-05", "a ancora segue a sua propria normalizacao"


def test_reentrada_e_detectada_pela_presenca_do_marcador_nao_pelo_seu_valor() -> None:
    """`has_glosas=False` tambem e reentrada: o que conta e o intake ja ter concluido."""
    out = identify_glosa_entry(
        _variaveis(
            has_glosas=False,
            data_recebimento_lote="2026-11-10",
            data_recebimento_lote_informada=None,
            data_recebimento_lote_informada_motivo=MOTIVO_DATA_INFORMADA_FUTURA,
        )
    )
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FUTURA


def test_reentrada_de_instancia_legada_e_nula_e_nunca_derivada_da_ancora() -> None:
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(_variaveis(has_glosas=True, data_recebimento_lote="2026-11-10"))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None, "nunca derivada da ancora, mesmo valida"
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_INSTANCIA_LEGADA
    [evento] = _eventos(logs)
    assert evento["motivo"] == MOTIVO_DATA_INFORMADA_INSTANCIA_LEGADA


def test_variavel_nova_sem_marcador_nao_e_reentrada() -> None:
    """A reentrada NAO e "a variavel nova existe": sem o marcador, a data e recalculada do lote."""
    out = identify_glosa_entry(
        _variaveis(data_recebimento_lote="2026-11-10", data_recebimento_lote_informada="2026-01-01")
    )
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] == "2026-11-10"


def test_o_marcador_e_uma_saida_do_proprio_intake() -> None:
    """`has_glosas` e escrito em TODA conclusao do intake — senao a reentrada seria indetectavel."""
    for variables in (_variaveis(), _variaveis(linhas_conta_refs=[])):
        assert MARCADOR_REENTRADA_INTAKE in identify_glosa_entry(variables)


# ---------------------------------------------------------------------------
# Log sem dado do titular; vocabulario fechado; escopo de fetch
# ---------------------------------------------------------------------------


def test_o_aviso_nunca_carrega_o_valor_bruto() -> None:
    bruto = "CPF 123.456.789-00 recebido 2026-11-10"
    with structlog.testing.capture_logs() as logs:
        identify_glosa_entry(_variaveis(data_recebimento_lote=bruto))
    [evento] = _eventos(logs)
    assert set(evento) == {"event", "log_level", "tenant_id", "numero_lote_tiss", "motivo"}
    assert evento["motivo"] == MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO
    assert bruto not in repr(evento)
    assert "123.456.789-00" not in repr(evento)


@pytest.mark.parametrize(
    "bruto", [None, "", "2026-11-10", "2026-11-18", "lixo", "2026-02-30"], ids=lambda v: repr(v)
)
def test_motivo_e_sempre_do_vocabulario_fechado(bruto: str | None) -> None:
    out = identify_glosa_entry(_variaveis(data_recebimento_lote=bruto))
    motivo = out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR]
    assert motivo is None or motivo in MOTIVOS_DATA_RECEBIMENTO_INFORMADA
    assert (motivo is None) is (out[DATA_RECEBIMENTO_INFORMADA_VAR] is not None)


def test_vocabulario_fechado_do_motivo() -> None:
    assert (
        frozenset(
            {"ausente", "formato_invalido", "data_futura", "instancia_legada", "valor_armazenado_invalido"}
        )
        == MOTIVOS_DATA_RECEBIMENTO_INFORMADA
    )


def test_escopo_de_fetch_do_intake_traz_as_variaveis_novas_e_o_marcador() -> None:
    escopo = contas._CONTAS_FETCH_SCOPES[TOPICO]
    for nome in (
        DATA_RECEBIMENTO_INFORMADA_VAR,
        DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR,
        MARCADOR_REENTRADA_INTAKE,
        "data_recebimento_lote",
    ):
        assert nome in escopo, f"{nome} fora do escopo de fetch de {TOPICO}: a reentrada nao o veria"


def test_montador_rco_segue_sem_produtor_para_a_data_de_recebimento() -> None:
    """DL-0174 nao liga a variavel crua ao montador: isso e outra unidade (depois de DL-0172)."""
    assert "dataRecebimentoLote" in contas.TERMOS_SEM_PRODUTOR_CONTAS


# ---------------------------------------------------------------------------
# Revisao independente: espacos so ASCII nas pontas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefixo_ou_sufixo",
    [
        ("\n", ""),
        ("", "\n"),
        ("\r", ""),
        ("\x0b", ""),
        ("\x0c", ""),
        ("\x1c", ""),
        ("", "\x1f"),
        (chr(0xA0), ""),
        ("", chr(0x3000)),
        (chr(0x2003), ""),
    ],
    ids=["lf", "lf_sufixo", "cr", "vt", "ff", "x1c", "x1f_sufixo", "nbsp", "u3000_sufixo", "em_space"],
)
def test_so_espaco_e_tab_ascii_sao_removidos_das_pontas(prefixo_ou_sufixo: tuple[str, str]) -> None:
    prefixo, sufixo = prefixo_ou_sufixo
    out = identify_glosa_entry(_variaveis(data_recebimento_lote=f"{prefixo}2026-11-10{sufixo}"))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO


def test_so_espacos_nao_ascii_nao_e_ausente_e_formato_invalido() -> None:
    out = identify_glosa_entry(_variaveis(data_recebimento_lote=chr(0xA0) * 3))
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO


# ---------------------------------------------------------------------------
# Revisao independente: o eco da reentrada e validado
# ---------------------------------------------------------------------------


_SEM_MOTIVO = object()


@pytest.mark.parametrize(
    ("valor", "motivo"),
    [
        ("2026-1-5", None),
        ("2026-11-10T00:00:00", None),
        ("2026-02-30", None),
        (" 2026-11-10", None),
        (20261110, None),
        ("2026-11-10", MOTIVO_DATA_INFORMADA_AUSENTE),
        (None, None),
        (None, "inventado"),
        (None, 7),
        (None, _SEM_MOTIVO),
    ],
    ids=[
        "sem_zero",
        "datetime",
        "data_impossivel",
        "espaco",
        "nao_texto",
        "valor_com_motivo",
        "nulo_sem_motivo",
        "motivo_fora_do_vocabulario",
        "motivo_nao_texto",
        "motivo_ausente",
    ],
)
def test_eco_de_valor_armazenado_invalido_vira_nulo_com_motivo_proprio(valor: Any, motivo: Any) -> None:
    extra: dict[str, Any] = {
        "has_glosas": True,
        "data_recebimento_lote": "2026-11-10",
        DATA_RECEBIMENTO_INFORMADA_VAR: valor,
    }
    if motivo is not _SEM_MOTIVO:
        extra[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] = motivo
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(_variaveis(**extra))
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None, "nunca ecoado nem derivado da ancora"
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_VALOR_ARMAZENADO_INVALIDO
    [evento] = _eventos(logs)
    assert set(evento) == {"event", "log_level", "tenant_id", "numero_lote_tiss", "motivo"}
    assert evento["motivo"] == MOTIVO_DATA_INFORMADA_VALOR_ARMAZENADO_INVALIDO


@pytest.mark.parametrize("motivo_armazenado", sorted(MOTIVOS_DATA_RECEBIMENTO_INFORMADA))
def test_eco_de_motivo_armazenado_nao_repete_o_aviso(motivo_armazenado: str) -> None:
    """O aviso so sai quando o par e RESOLVIDO nesta execucao; ecoar um motivo armazenado (inclusive
    `instancia_legada` e `valor_armazenado_invalido`) nao avisa de novo."""
    with structlog.testing.capture_logs() as logs:
        out = identify_glosa_entry(
            _variaveis(
                has_glosas=True,
                data_recebimento_lote=HOJE.isoformat(),
                data_recebimento_lote_informada=None,
                data_recebimento_lote_informada_motivo=motivo_armazenado,
            )
        )
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] is None
    assert out[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == motivo_armazenado
    assert not _eventos(logs)


def test_legada_avisa_so_na_resolucao_e_nao_no_eco_seguinte() -> None:
    with structlog.testing.capture_logs() as logs:
        primeira = identify_glosa_entry(_variaveis(has_glosas=True, data_recebimento_lote="2026-11-10"))
    assert len(_eventos(logs)) == 1
    with structlog.testing.capture_logs() as logs:
        segunda = identify_glosa_entry(_variaveis(**primeira))
    assert segunda[DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR] == MOTIVO_DATA_INFORMADA_INSTANCIA_LEGADA
    assert not _eventos(logs)


def test_reentrada_com_ancora_nova_da_mensagem_de_correcao_mantem_a_data_informada() -> None:
    """A ancora pode mudar (a correcao trouxe outra `data_recebimento_lote`); a data informada nao."""
    primeira = identify_glosa_entry(_variaveis(data_recebimento_lote="2026-11-10"))
    correcao = _variaveis(**{**primeira, "data_recebimento_lote": "2026-11-12"})
    out = identify_glosa_entry(correcao)
    assert out["data_recebimento_lote"] == "2026-11-12"
    assert out[DATA_RECEBIMENTO_INFORMADA_VAR] == "2026-11-10"


# ---------------------------------------------------------------------------
# Revisao independente: o marcador de reentrada nunca e semeado no start
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parents[4]
_BPMN = _REPO / "spec" / "processes" / "bpmn" / "SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}
_PROIBIDAS_ANTES_DO_INTAKE = (
    MARCADOR_REENTRADA_INTAKE,
    DATA_RECEBIMENTO_INFORMADA_VAR,
    DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR,
)


def test_marcador_fora_das_entradas_de_start_da_marina() -> None:
    assert MARCADOR_REENTRADA_INTAKE not in marina_graph._CALLER_INPUT_FIELDS
    assert MARCADOR_REENTRADA_INTAKE not in marina_delegation._CONTAS_BOOLEAN_META_KEYS
    campos = {f.name for f in engine_schemas.MARINA_CONTAS_START.fields}
    assert "numero_lote_tiss" in campos, "sanidade: o schema de start de CONTAS e o certo"
    for proibida in _PROIBIDAS_ANTES_DO_INTAKE:
        assert proibida not in campos


def test_marcador_fora_das_variaveis_de_start_da_marina() -> None:
    graph = marina_graph.MarinaGraph(
        inference=cast(Any, None), dmn=cast(Any, None), cibseven=cast(Any, None), audit_sink=cast(Any, None)
    )
    estado: dict[str, Any] = {
        "flow": "contas",
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-1",
        "prestador_id": "prestador-1",
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
        "numero_conta": "CONTA-1",
        "competencia": "2026-11",
        "data_recebimento_lote": "2026-11-10",
        "valor_apresentado_brl": 100.0,
        "tipo_lote": "sadt",
        "divergencia_valor": True,
        "has_glosas": True,  # mesmo que o estado o carregue, o start nao o escreve
        "route": "human_review",
        "dmn_refs": {"glosa_triage": "r1"},
    }
    variaveis = graph._contract_variables(cast(Any, estado))
    assert variaveis["numero_lote_tiss"] == "LOTE-1", "sanidade: o fluxo contas foi o exercitado"
    for proibida in _PROIBIDAS_ANTES_DO_INTAKE:
        assert proibida not in variaveis


def _elementos_antes_do_intake(processo: ET.Element) -> set[str]:
    fluxos = [(f.get("sourceRef"), f.get("targetRef")) for f in processo.findall("bpmn:sequenceFlow", _NS)]
    pendentes = [str(e.get("id")) for e in processo.findall("bpmn:startEvent", _NS)]
    vistos: set[str] = set()
    while pendentes:
        atual = pendentes.pop()
        if atual in vistos or atual == "ST_ApurarDivergencias":
            continue
        vistos.add(atual)
        pendentes.extend(str(alvo) for origem, alvo in fluxos if origem == atual and alvo)
    return vistos


def test_marcador_nao_e_inicializado_antes_do_intake_no_bpmn() -> None:
    processo = ET.parse(_BPMN).getroot().find("bpmn:process", _NS)
    assert processo is not None
    antes = _elementos_antes_do_intake(processo)
    assert {"Start_LoteTissRecebido", "ST_PublishReceived"} <= antes
    nomes: set[str] = set()
    for elemento in processo.iter():
        if elemento.get("id") not in antes:
            continue
        for tag in ("outputParameter", "inputParameter", "out", "in"):
            for parametro in elemento.iter(f"{{{_NS['camunda']}}}{tag}"):
                nomes.add(str(parametro.get("name") or parametro.get("target") or ""))
    assert "vencimento_ausente" in nomes, "sanidade: os parametros de ST_PublishReceived foram lidos"
    for proibida in _PROIBIDAS_ANTES_DO_INTAKE:
        assert proibida not in nomes, f"{proibida} inicializada antes de ST_ApurarDivergencias"
