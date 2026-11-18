"""E-SF-07 (EP-REG-32a): registro append-only dos eventos-candidatos ao inicio do relogio, sem conteudo."""

from __future__ import annotations

import ast
import hashlib
import inspect
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway import commercial_t0_ledger as lg
from maezo.gateway.commercial_t0_ledger import (
    EventoCandidato,
    RegistroEmMemoriaSoParaTeste,
    T0LedgerRecusado,
    digest_do_proponente,
    registrar_evento_candidato,
)

_RAIZ = Path(lg.__file__).resolve().parents[3]
_CONTRATO = _RAIZ / "tests" / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
T1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _relogio(*instantes: datetime) -> Any:
    fila = list(instantes)
    return lambda: fila.pop(0)


def test_t0_ledger_records_first_prospect_save() -> None:
    reg = RegistroEmMemoriaSoParaTeste()
    primeiro = registrar_evento_candidato(
        reg, prospect_ref="prop-1", tipo="prospect_save_com_valores", relogio_do_servidor=_relogio(T1)
    )
    segundo = registrar_evento_candidato(
        reg,
        prospect_ref="prop-1",
        tipo="prospect_save_com_valores",
        relogio_do_servidor=_relogio(T1 + timedelta(days=3)),
    )
    assert segundo == primeiro and segundo.instante == T1
    assert reg.eventos(digest_do_proponente("prop-1")) == (primeiro,)
    # Gravar outro instante para o mesmo par direto na persistencia: recusa tipada, nada reescrito.
    with pytest.raises(T0LedgerRecusado) as e:
        reg.anexar(
            EventoCandidato(primeiro.proponente_digest, "prospect_save_com_valores", T1 + timedelta(1))
        )
    assert e.value.code == "t0_ledger_immutable"
    assert reg.eventos(primeiro.proponente_digest) == (primeiro,)


def test_t0_ledger_sem_conteudo() -> None:
    assert {f for f in EventoCandidato.__slots__} == {"proponente_digest", "tipo", "instante"}
    reg = RegistroEmMemoriaSoParaTeste()
    ev = registrar_evento_candidato(
        reg, prospect_ref="gestante-hipertensa-01", tipo="party_submit", relogio_do_servidor=_relogio(T1)
    )
    # A referencia nunca e' guardada: so' o digest dela.
    assert ev.proponente_digest == hashlib.sha256(b"gestante-hipertensa-01").hexdigest()
    assert "gestante" not in repr(ev)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tipo", "lembrete_de_recuperacao"),
        ("tipo", "declaracao_de_saude"),
        ("instante", datetime(2026, 11, 1, 12, 0)),
        ("instante", datetime(2026, 11, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))),
        ("instante", "2026-11-01T12:00:00Z"),
        ("proponente_digest", "prop-1"),
        ("proponente_digest", "A" * 64),
    ],
)
def test_evento_fora_da_classe_recusado(campo: str, valor: Any) -> None:
    base: dict[str, Any] = {"proponente_digest": "0" * 64, "tipo": "party_submit", "instante": T1}
    base[campo] = valor
    with pytest.raises(T0LedgerRecusado) as e:
        RegistroEmMemoriaSoParaTeste().anexar(EventoCandidato(**base))
    assert e.value.code == "t0_ledger_evento_invalido"
    assert str(valor) not in str(e.value)


@pytest.mark.parametrize("ref", ["", None, 5, "x" * 129])
def test_referencia_invalida_recusa(ref: Any) -> None:
    with pytest.raises(T0LedgerRecusado) as e:
        registrar_evento_candidato(
            RegistroEmMemoriaSoParaTeste(),
            prospect_ref=ref,
            tipo="party_submit",
            relogio_do_servidor=_relogio(T1),
        )
    assert e.value.code == "t0_ledger_ref_invalida"


def test_suspensoes_registradas_sem_substituir_inicio() -> None:
    reg = RegistroEmMemoriaSoParaTeste()
    for i in range(2):
        registrar_evento_candidato(
            reg,
            prospect_ref="prop-2",
            tipo="pedido_de_documentacao",
            relogio_do_servidor=_relogio(T1 + timedelta(days=i)),
        )
    assert len(reg.eventos(digest_do_proponente("prop-2"))) == 2


def test_wire_unchanged() -> None:
    # Nenhum campo de instante entra no contrato candidato: o modulo nao le nem escreve o arquivo do contrato.
    fonte = Path(inspect.getfile(lg)).read_text(encoding="utf-8")
    assert "contract-c6-4" not in fonte and "candidate_artifacts" not in fonte
    assert _CONTRATO.is_file()


def test_nenhum_composto_de_producao_instancia_a_memoria() -> None:
    src = _RAIZ / "src" / "maezo"
    usos = []
    for p in src.rglob("*.py"):
        if p.resolve() == Path(lg.__file__).resolve():
            continue
        texto = p.read_text(encoding="utf-8")
        if "RegistroEmMemoriaSoParaTeste" in texto or "commercial_t0_ledger" in texto:
            usos.append(str(p.relative_to(_RAIZ)))
    # O unico consumidor permitido e' o relogio (que so' le o tipo do evento), e ele nao instancia a memoria.
    assert usos == ["src/maezo/tools/workers/rn413_contracting_clock.py"]
    relogio = (src / "tools" / "workers" / "rn413_contracting_clock.py").read_text(encoding="utf-8")
    assert "RegistroEmMemoriaSoParaTeste" not in relogio


def test_sem_assert_e_sem_nome_de_sinal_na_fonte() -> None:
    arvore = ast.parse(Path(inspect.getfile(lg)).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.Assert)]
