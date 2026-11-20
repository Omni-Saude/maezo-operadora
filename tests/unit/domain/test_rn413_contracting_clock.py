"""E-SF-07 (TB-E-09-a, EP-REG-32b, EP-REG-23c): um relogio-sombra so', nunca adverso, nunca exibido."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maezo.domain import rn413_contracting_clock as rc
from maezo.domain.rn413_contracting_clock import (
    EstadoDoRelogio,
    EventoCandidato,
    RelogioRecusado,
    estado_do_relogio,
    itens_de_escalonamento,
)

_RAIZ = Path(rc.__file__).resolve().parents[3]
_FONTE = Path(inspect.getfile(rc)).read_text(encoding="utf-8")
D = "1" * 64
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _ev(tipo: str, dias: int, digest: str = D) -> EventoCandidato:
    return EventoCandidato(digest, tipo, INICIO + timedelta(days=dias))


def _estado(eventos: list[EventoCandidato], dia: int) -> EstadoDoRelogio:
    return estado_do_relogio(eventos, proponente_digest=D, agora=INICIO + timedelta(days=dia))


def test_shadow_clock_starts_at_first_prospect_save() -> None:
    eventos = [_ev("party_submit", 4), _ev("prospect_save_com_valores", 0), _ev("acceptance_record", 9)]
    assert _estado(eventos, 10).inicio == INICIO
    # Sem prospect.save, o relogio usa o candidato mais cedo que existir; nunca um posterior.
    assert _estado([_ev("acceptance_record", 9), _ev("party_submit", 4)], 10).inicio == INICIO + timedelta(4)


def test_clock_not_suspended_by_marketing_reminder() -> None:
    # Lembrete de recuperacao nao e' evento do registro: nao existe como tipo, logo nao suspende nada.
    from maezo.domain.rn413_contracting_clock import TIPOS_DE_EVENTO

    assert not any("lembrete" in t or "recupera" in t for t in TIPOS_DE_EVENTO)
    assert _estado([_ev("prospect_save_com_valores", 0)], 24).dias_corridos == 24


def test_rn413_monitor_ignores_suspension_until_ratified() -> None:
    eventos = [
        _ev("prospect_save_com_valores", 0),
        _ev("pedido_de_documentacao", 3),
        _ev("pericia_ou_entrevista", 5),
    ]
    estado = _estado(eventos, 20)
    assert estado.dias_corridos == 20 and estado.suspensoes_registradas == 2


@pytest.mark.parametrize(("dia", "alarmes"), [(14, ()), (15, (15,)), (20, (15, 20)), (23, (15, 20, 23))])
def test_clock_alerts_before_day_25(dia: int, alarmes: tuple[int, ...]) -> None:
    estado = _estado([_ev("prospect_save_com_valores", 0)], dia)
    assert estado.alarmes_atingidos == alarmes and estado.possivel_vigencia_automatica is False
    itens = itens_de_escalonamento(estado, estagio="cadastro", grupo_responsavel="especialista_comercial")
    assert [i.dia for i in itens] == list(alarmes)
    assert all(i.motivo == "alarme_relogio_sombra" for i in itens)


def test_rn413_monitor_alerts_on_shadow_clock_without_anchor() -> None:
    # Sem ancora legal (nao existe neste modulo), o alarme vem do relogio-sombra: nunca silencio.
    estado = _estado([_ev("acceptance_record", 0)], 24)
    itens = itens_de_escalonamento(estado, estagio="aceite", grupo_responsavel="especialista_comercial")
    assert len(itens) == 3 and {i.motivo for i in itens} == {"alarme_relogio_sombra"}


def test_rn413_monitor_never_adverse() -> None:
    estado = _estado([_ev("prospect_save_com_valores", 0)], 40)
    assert estado.possivel_vigencia_automatica is True and estado.recuperacao_bloqueada is True
    itens = itens_de_escalonamento(estado, estagio="cadastro", grupo_responsavel="especialista_comercial")
    assert itens[-1].motivo == "possivel_vigencia_automatica"
    assert {i.acao for i in itens} == {"escalation_requested"}
    nomes = {
        n.name for n in ast.walk(ast.parse(_FONTE)) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for adverso in ("rescind", "negar", "cobrar", "bloquear", "terminat", "deny", "cancel"):
        assert not any(adverso in n for n in nomes)


def test_sem_inicio_sem_contagem_e_sem_item() -> None:
    estado = _estado([_ev("pedido_de_documentacao", 0)], 30)
    assert (
        estado.inicio is None
        and estado.dias_corridos is None
        and estado.possivel_vigencia_automatica is False
    )
    assert (
        itens_de_escalonamento(estado, estagio="cadastro", grupo_responsavel="especialista_comercial") == ()
    )


def test_recuperacao_bloqueada_a_partir_do_dia_20() -> None:
    assert _estado([_ev("prospect_save_com_valores", 0)], 19).recuperacao_bloqueada is False
    assert _estado([_ev("prospect_save_com_valores", 0)], 20).recuperacao_bloqueada is True


def test_eventos_de_outro_proponente_ignorados() -> None:
    estado = _estado([_ev("prospect_save_com_valores", 0, digest="2" * 64)], 30)
    assert estado.inicio is None


def test_instante_sem_utc_recusa() -> None:
    with pytest.raises(RelogioRecusado) as e:
        estado_do_relogio([], proponente_digest=D, agora=datetime(2026, 11, 1))
    assert e.value.code == "relogio_instante_invalido"


def test_estado_sem_campo_de_data_limite() -> None:
    campos = {f.name for f in dataclasses.fields(EstadoDoRelogio)}
    for proibido in ("limite", "prazo", "vence", "deadline", "eta", "data_final"):
        assert not any(proibido in c for c in campos)


def test_limiares_num_unico_lugar() -> None:
    """15, 20, 23 e 25 aparecem uma vez cada, nas constantes; nenhum outro arquivo de src os define."""
    for numero in ("15", "20", "23", "25"):
        assert len(re.findall(rf"(?<![\w.]){numero}(?![\w.])", _FONTE)) == 1, numero
    assert "operacional, relogio-sombra, NAO e' prazo legal" in _FONTE
    outros = []
    for p in (_RAIZ / "src" / "maezo").rglob("*.py"):
        if p.resolve() == Path(rc.__file__).resolve():
            continue
        texto = p.read_text(encoding="utf-8")
        if re.search(r"DIA_(ALARME|CORTE)|rn413_contracting", texto):
            outros.append(str(p.relative_to(_RAIZ)))
    assert sorted(outros) == [
        "src/maezo/gateway/commercial_pos_dia_25.py",
        "src/maezo/gateway/commercial_t0_ledger.py",
        # E-SF-16: consumidores do estado canonico; nao definem limiar.
        "src/maezo/gateway/communications/recovery_dispatch.py",
        "src/maezo/gateway/communications/recovery_eligibility.py",
    ]


def test_dominio_puro_sem_importar_outra_camada() -> None:
    arvore = ast.parse(_FONTE)
    modulos = [n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)]
    modulos += [a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in modulos if m.startswith("maezo")]


def test_nao_registrado_no_runtime() -> None:
    workers = _RAIZ / "src" / "maezo" / "tools" / "workers"
    for nome in ("harness.py", "__init__.py", "bootstrap.py"):
        assert "rn413_contracting_clock" not in (workers / nome).read_text(encoding="utf-8")
    runtime = _RAIZ / "src" / "maezo" / "runtime"
    assert not [
        p for p in runtime.rglob("*.py") if "rn413_contracting_clock" in p.read_text(encoding="utf-8")
    ]


def test_virada_de_data_utc() -> None:
    s = datetime(2026, 11, 1, 23, 59, tzinfo=UTC)
    e = estado_do_relogio(
        [EventoCandidato(D, "party_submit", s)], proponente_digest=D, agora=s + timedelta(minutes=2)
    )
    assert e.dias_corridos == 1


def test_inicio_a_noite_no_brasil_conta_pelo_dia_civil_local() -> None:
    # 01:00 UTC de 2/11 e' 22:00 de 1/11 em Brasilia: o dia do inicio e' 1/11, nao 2/11.
    s = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    e = estado_do_relogio(
        [EventoCandidato(D, "party_submit", s)],
        proponente_digest=D,
        agora=datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
    )
    assert e.dias_corridos == 25 and e.possivel_vigencia_automatica is True


def test_instante_de_evento_sem_utc_recusa() -> None:
    from datetime import timezone

    s = datetime(2026, 11, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    with pytest.raises(RelogioRecusado) as e:
        estado_do_relogio([EventoCandidato(D, "party_submit", s)], proponente_digest=D, agora=INICIO)
    assert e.value.code == "relogio_instante_invalido"


def test_estado_adulterado_nao_passa_por_canonico() -> None:
    import dataclasses

    real = _estado([_ev("prospect_save_com_valores", 0)], 30)
    adulterado = dataclasses.replace(real, possivel_vigencia_automatica=False, dias_corridos=3)
    with pytest.raises(RelogioRecusado) as e:
        itens_de_escalonamento(adulterado, estagio="cadastro", grupo_responsavel="especialista_comercial")
    assert e.value.code == "SECOND_CLOCK_FORBIDDEN"
