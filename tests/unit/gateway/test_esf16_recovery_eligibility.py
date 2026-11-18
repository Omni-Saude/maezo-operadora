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


def test_os_dois_modulos_nao_redefinem_limiar_do_relogio() -> None:
    # Os limiares (15, 20, 23, 25) vivem so' nas constantes do relogio canonico; aqui so' se le o estado.
    from maezo.gateway.communications import recovery_dispatch as rd

    for modulo in (re_mod, rd):
        fonte = inspect.getsource(modulo)
        assert not re.search(r"(?<![\w.])(15|20|23|25)(?![\w.])", fonte), modulo.__name__
        assert not re.search(r"DIA_(ALARME|CORTE|BLOQUEIO)", fonte), modulo.__name__


def test_sem_importador_em_src() -> None:
    usos = [
        str(p.relative_to(_RAIZ))
        for p in (_RAIZ / "src" / "maezo").rglob("*.py")
        if p.resolve() != Path(re_mod.__file__).resolve()
        and "recovery_eligibility" in p.read_text(encoding="utf-8")
    ]
    # So' a guarda de despacho (nao montada) o importa.
    assert usos == ["src/maezo/gateway/communications/recovery_dispatch.py"]
