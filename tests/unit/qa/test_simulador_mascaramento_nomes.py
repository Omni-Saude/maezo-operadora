"""Privacidade do simulador (avaliacao de 21/10/2026): o nome do dependente lido da AMH sai mascarado em TODA
saida gravada, em qualquer formato — o rotulo "Para <nome>", frases livres e as listas fixas. Nomes
SINTETICOS."""

from __future__ import annotations

import pytest

from maezo.agents.helena import consultas_plano as cp
from maezo.qa.simulador.mascaramento import TOKEN_NOME_DEPENDENTE, Mascarador


@pytest.fixture
def registro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "_NOMES_LIDOS", None)
    cp.registrar_nomes_lidos()


def test_nome_lido_da_amh_e_mascarado_em_qualquer_formato(registro: None) -> None:
    cp.NomesDoTurno({1: "BEATRIZ QUINTELA DE ARAUJO TESTE"})
    m = Mascarador()
    for texto in (
        "Para Beatriz Quintela de Araujo Teste, pelo cadastro do seu plano: ...",
        "A Beatriz já pode fazer o exame.",
        "Consultei para BEATRIZ QUINTELA ARAUJO.",
    ):
        saida = m.texto(texto)
        assert "Beatriz" not in saida and "BEATRIZ" not in saida and "Quintela" not in saida.title()
        assert TOKEN_NOME_DEPENDENTE in saida


def test_rotulo_para_alguem_mascara_mesmo_sem_registro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "_NOMES_LIDOS", None)
    saida = Mascarador().texto("Para Ana Teste Sintetica, pelo cadastro do seu plano: consta.")
    assert "Ana" not in saida and TOKEN_NOME_DEPENDENTE in saida
    assert Mascarador().texto("Para você (titular), pelo cadastro do seu plano").startswith("Para você")


def test_sem_registro_nada_e_guardado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "_NOMES_LIDOS", None)
    cp.NomesDoTurno({1: "Nome Qualquer Teste"})
    assert cp.nomes_lidos() == frozenset()
