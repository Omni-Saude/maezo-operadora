"""DL-0174 — `data_recebimento_lote_informada`: a data de recebimento CRUA do lote (SP-OP-CONTAS-001).

A ancora `data_recebimento_lote` e defaultada para HOJE/UTC pelo intake quando o lote nao a traz (os
timers de SLA precisam dela) e, na reentrada por `BME_LinhasAtualizadas`, o valor padrao fica
indistinguivel do recebido. A variavel nova guarda a data EXATAMENTE como o lote a informou:
anulavel, nunca defaultada, ecoada na reentrada (detectada por um FATO DO PROCESSO, `has_glosas`),
`None`/`instancia_legada` numa instancia anterior a esta mudanca. A ancora nao muda.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import structlog

from maezo.tools.workers import contas
from maezo.tools.workers.contas import (
    DATA_RECEBIMENTO_INFORMADA_MOTIVO_VAR,
    DATA_RECEBIMENTO_INFORMADA_VAR,
    MARCADOR_REENTRADA_INTAKE,
    MOTIVO_DATA_INFORMADA_AUSENTE,
    MOTIVO_DATA_INFORMADA_FORMATO_INVALIDO,
    MOTIVO_DATA_INFORMADA_FUTURA,
    MOTIVO_DATA_INFORMADA_INSTANCIA_LEGADA,
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
        frozenset({"ausente", "formato_invalido", "data_futura", "instancia_legada"})
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
