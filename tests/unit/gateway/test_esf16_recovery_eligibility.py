"""E-SF-16 (GTM-E-51): elegibilidade de recuperacao. Dados sinteticos; nada envia."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maezo.domain.rn413_contracting_clock import (
    DIA_BLOQUEIO_DA_RECUPERACAO,
    EstadoDoRelogio,
    EventoCandidato,
    RelogioRecusado,
    estado_do_relogio,
)
from maezo.gateway.communications import recovery_eligibility as re_mod
from maezo.gateway.communications.recovery_eligibility import (
    ETAPAS,
    ETAPAS_CANDIDATAS,
    ETAPAS_NUNCA,
    TITULARES_DA_RECUPERACAO,
    Elegivel,
    MotivoRecuperacao,
    RecuperacaoRecusada,
    avaliar_elegibilidade,
)

D = "5" * 64
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_RAIZ = Path(re_mod.__file__).resolve().parents[4]


def _relogio(dia: int | None) -> EstadoDoRelogio:
    eventos = [] if dia is None else [EventoCandidato(D, "prospect_save_com_valores", INICIO)]
    return estado_do_relogio(eventos, proponente_digest=D, agora=INICIO + timedelta(days=dia or 0))


def _avaliar(**kw: object) -> Elegivel:
    base: dict[str, object] = {
        "tipo_titular": "prospect",
        "etapa": "cotacao_sem_decisao",
        "aceite_registrado": False,
        "relogio": _relogio(3),
    }
    base.update(kw)
    return avaliar_elegibilidade(**base)  # type: ignore[arg-type]


def _motivo(**kw: object) -> MotivoRecuperacao:
    with pytest.raises(RecuperacaoRecusada) as e:
        _avaliar(**kw)
    assert str(e.value) == e.value.motivo.value
    return e.value.motivo


def test_etapas_em_classe_fechada() -> None:
    assert frozenset({"cotacao_sem_decisao", "proposta_incompleta", "termos_sem_aceite"}) == ETAPAS_CANDIDATAS
    assert frozenset({"aceite_sem_pagamento", "contrato_pendente_declaracao_de_saude"}) == ETAPAS_NUNCA
    assert ETAPAS_CANDIDATAS | ETAPAS_NUNCA == ETAPAS
    assert not ETAPAS_CANDIDATAS & ETAPAS_NUNCA


@pytest.mark.parametrize("etapa", sorted(ETAPAS_CANDIDATAS))
@pytest.mark.parametrize("tipo", ["lead", "prospect"])
def test_lead_e_prospect_iguais_dentro_do_relogio(tipo: str, etapa: str) -> None:
    assert _avaliar(tipo_titular=tipo, etapa=etapa) == Elegivel(proponente_digest=D, etapa=etapa)


@pytest.mark.parametrize(
    "tipo", ["beneficiario", "Prospect", "lead ", "", None, 1, type("S", (str,), {})("prospect")]
)
def test_beneficiario_e_tipo_fora_nunca_recebem_recuperacao(tipo: object) -> None:
    assert _motivo(tipo_titular=tipo) is MotivoRecuperacao.RECOVERY_SUBJECT_KIND_NOT_ALLOWED
    assert frozenset({"lead", "prospect"}) == TITULARES_DA_RECUPERACAO


@pytest.mark.parametrize("etapa", sorted(ETAPAS_NUNCA))
def test_etapas_depois_do_aceite_nunca(etapa: str) -> None:
    assert _motivo(etapa=etapa) is MotivoRecuperacao.RECOVERY_AFTER_ACCEPTANCE


@pytest.mark.parametrize("aceite", [True, None, 0, 1, "nao"])
def test_aceite_registrado_ou_desconhecido_recusa(aceite: object) -> None:
    assert _motivo(aceite_registrado=aceite) is MotivoRecuperacao.RECOVERY_AFTER_ACCEPTANCE


@pytest.mark.parametrize("etapa", ["", "cotacao", "pagamento", None, "Cotacao_sem_decisao"])
def test_etapa_desconhecida_ou_sem_fonte_recusa(etapa: object) -> None:
    assert _motivo(etapa=etapa) is MotivoRecuperacao.RECOVERY_STAGE_UNKNOWN


def test_recovery_refuses_when_clock_anchor_unresolved() -> None:
    assert _motivo(relogio=_relogio(None)) is MotivoRecuperacao.CLOCK_ANCHOR_UNRESOLVED


def test_recovery_refuses_at_or_after_shadow_clock_d25_from_first_prospect_save() -> None:
    # S1 (conservador): bloqueia ja' no dia em que o relogio-sombra bloqueia a recuperacao, antes do 25.
    for dia in (DIA_BLOQUEIO_DA_RECUPERACAO, 24, 25, 26, 40):
        assert _motivo(relogio=_relogio(dia)) is MotivoRecuperacao.RECOVERY_CLOCK_BLOCKED, dia
    assert _avaliar(relogio=_relogio(DIA_BLOQUEIO_DA_RECUPERACAO - 1)).etapa == "cotacao_sem_decisao"


def test_estado_de_outro_relogio_recusado() -> None:
    falso = dataclasses.replace(_relogio(3))
    with pytest.raises(RelogioRecusado) as e:
        _avaliar(relogio=falso)
    assert e.value.code == "SECOND_CLOCK_FORBIDDEN"
    with pytest.raises(RelogioRecusado):
        _avaliar(relogio=dataclasses.replace(_relogio(30), recuperacao_bloqueada=False))


def test_assinatura_fechada_sem_idade_plano_valor_ou_saude() -> None:
    parametros = inspect.signature(avaliar_elegibilidade).parameters
    assert set(parametros) == {"tipo_titular", "etapa", "aceite_registrado", "relogio"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parametros.values())
    assert {f.name for f in dataclasses.fields(Elegivel)} == {"proponente_digest", "etapa"}
    proibidos = re.compile(r"idade|faixa|nascimento|plano|valor|preco|saude_|cid|cpt|sinistr", re.I)
    assert not [n for n in parametros if proibidos.search(n)]


def test_nunca_chama_aceite_contrato_ou_matricula_e_sem_assert() -> None:
    fonte = inspect.getsource(re_mod)
    arvore = ast.parse(fonte)
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.Assert)]
    chamadas = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
    }
    assert not chamadas & {"record", "issue", "request", "acceptance_record", "contract_issue"}
    assert not re.search(r"\bret\.\w+\.\w+", fonte)


_LIMIARES_DO_RELOGIO = frozenset({15, 20, 23, 25})
#: Atributos do estado do relogio que os dois modulos podem ler. Nada de `dias_corridos` nem alarmes.
_ATRIBUTOS_LIDOS_PERMITIDOS = frozenset({"recuperacao_bloqueada", "inicio", "proponente_digest"})
_ATRIBUTOS_DO_ESTADO = frozenset(
    {
        "dias_corridos",
        "alarmes_atingidos",
        "possivel_vigencia_automatica",
        "suspensoes_registradas",
        "recuperacao_bloqueada",
        "inicio",
        "proponente_digest",
    }
)


def _violacoes_de_limiar(fonte: str) -> list[str]:
    """Por AST: numero de limiar, constante do relogio, comparacao com a contagem ou leitura proibida."""
    achados: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Constant) and type(no.value) is int and no.value in _LIMIARES_DO_RELOGIO:
            achados.append(f"numero {no.value}")
        if isinstance(no, ast.Name) and re.match(r"DIA_|DIAS_DE_ALARME", no.id):
            achados.append(f"constante {no.id}")
        if isinstance(no, ast.alias) and re.match(r"DIA_|DIAS_DE_ALARME", no.name):
            achados.append(f"import {no.name}")
        if isinstance(no, ast.Attribute) and no.attr in _ATRIBUTOS_DO_ESTADO - _ATRIBUTOS_LIDOS_PERMITIDOS:
            achados.append(f"leitura {no.attr}")
        if isinstance(no, ast.Compare):
            lados = [no.left, *no.comparators]
            nomes = {n.attr for lado in lados for n in ast.walk(lado) if isinstance(n, ast.Attribute)} | {
                n.id for lado in lados for n in ast.walk(lado) if isinstance(n, ast.Name)
            }
            # Unica comparacao admitida com o estado: a ancora ausente (`inicio is None` / `is not None`).
            so_ancora = all(isinstance(op, (ast.Is, ast.IsNot)) for op in no.ops) and any(
                isinstance(c, ast.Constant) and c.value is None for c in no.comparators
            )
            if nomes & {"dias_corridos", "alarmes_atingidos"} or ("inicio" in nomes and not so_ancora):
                achados.append("comparacao com contagem do relogio")
    return achados


def test_os_dois_modulos_nao_redefinem_limiar_do_relogio() -> None:
    # CD-171/CD-173: so' leem `recuperacao_bloqueada` (e a ancora/digest), nunca comparam nem definem limiar.
    from maezo.gateway.communications import recovery_dispatch as rd

    for modulo in (re_mod, rd):
        assert _violacoes_de_limiar(inspect.getsource(modulo)) == [], modulo.__name__
    leituras = {
        n.attr
        for m in (re_mod, rd)
        for n in ast.walk(ast.parse(inspect.getsource(m)))
        if isinstance(n, ast.Attribute) and n.attr in _ATRIBUTOS_DO_ESTADO
    }
    assert "recuperacao_bloqueada" in leituras  # a leitura real existe (a varredura nao e' vazia)


@pytest.mark.parametrize(
    "trecho",
    [
        "x = 20",
        "if estado.dias_corridos >= 20: pass",
        "if estado.dias_corridos > limite: pass",
        "from maezo.domain.rn413_contracting_clock import DIA_CORTE",
        "y = DIA_ALARME_2",
        "z = estado.alarmes_atingidos",
        "if estado.inicio < agora: pass",
    ],
)
def test_a_varredura_de_limiar_pega_cada_forma(trecho: str) -> None:
    assert _violacoes_de_limiar(trecho)


def test_sem_importador_em_src() -> None:
    usos = [
        str(p.relative_to(_RAIZ))
        for p in (_RAIZ / "src" / "maezo").rglob("*.py")
        if p.resolve() != Path(re_mod.__file__).resolve()
        and "recovery_eligibility" in p.read_text(encoding="utf-8")
    ]
    # So' a guarda de despacho (nao montada) o importa.
    assert usos == ["src/maezo/gateway/communications/recovery_dispatch.py"]
