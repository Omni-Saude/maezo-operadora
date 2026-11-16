"""A composicao do K2 na construcao do grafo da Helena (DL-0167; combinado T-MZO-A/T-MZO-B)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena import conversa, graph
from maezo.agents.helena.habilidades import cobertura as cob


class _PacoteFalso:
    def __init__(self, juntar: tuple[float, frozenset[str]] | None = None) -> None:
        self._juntar = juntar

    def juntar_mensagens(self) -> tuple[float, frozenset[str]] | None:
        return self._juntar

    def aplicar_no_grafo(self, config: Any) -> dict[str, Any]:
        return {**config, "pacote_aplicado": True}


def _entrada(pacote: _PacoteFalso) -> conversa.HelenaDeEntrada:
    entrada = conversa.HelenaDeEntrada()
    entrada._pacote_da_helena = pacote  # type: ignore[assignment]
    return entrada


def test_compor_cobertura_roda_depois_do_pacote_e_antes_do_build(monkeypatch: pytest.MonkeyPatch) -> None:
    ordem: list[tuple[str, dict[str, Any]]] = []

    def compor(raiz: Path, cfg: dict[str, Any], catalogo: tuple[()] = ()) -> None:
        assert catalogo == ()
        ordem.append(("compor", dict(cfg)))
        cfg["marca_da_composicao"] = True

    def build(cfg: dict[str, Any]) -> str:
        ordem.append(("build", dict(cfg)))
        return "grafo"

    monkeypatch.setattr(conversa, "compor_cobertura", compor)
    monkeypatch.setattr(graph, "build", build)
    assert _entrada(_PacoteFalso()).construir_grafo({"x": 1}) == "grafo"
    assert [nome for nome, _ in ordem] == ["compor", "build"]
    assert ordem[0][1]["pacote_aplicado"] is True  # o aceite de nao ratificado sai do pacote
    assert ordem[1][1]["marca_da_composicao"] is True  # o build recebe a cfg composta


def test_com_juntar_a_composicao_tambem_roda(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []

    def compor(raiz: Path, cfg: dict[str, Any], catalogo: tuple[()] = ()) -> None:
        chamadas.append(dict(cfg))

    monkeypatch.setattr(conversa, "compor_cobertura", compor)
    monkeypatch.setattr(graph, "build", lambda cfg: cfg)
    monkeypatch.setattr(conversa._juntar, "remetente_que_junta", lambda remetente: "remetente_com_trava")
    cfg = _entrada(_PacoteFalso(juntar=(2.0, frozenset()))).construir_grafo({"whatsapp": "w"})
    assert chamadas and cfg["whatsapp"] == "remetente_com_trava"


def test_lock_comitado_em_sem_pacote_nao_registra_k2(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lock ausente (antes do #1249) ou em `sem_pacote`: a composicao e' no-op e nada do K2 entra na cfg.
    monkeypatch.setattr(graph, "build", lambda cfg: cfg)
    cfg = _entrada(_PacoteFalso()).construir_grafo({})
    assert cob.CHAVE_DO_HASH_K2 not in cfg
    camadas = cfg.get(cob.CHAVE_DAS_CAMADAS) or ()
    assert not [c for c in camadas if getattr(c, "camada", None) == "regra_geral"]
