"""Pacote de comportamento da Helena (ADR-0067 fase 3): validacao estrita e o pacote vencendo a plataforma."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena import conversa, graph
from maezo.agents.helena import pacote as hp
from maezo.runtime import pacote_do_agente as pda

_RAIZ = Path(__file__).resolve().parents[3]
_SPEC = _RAIZ / "spec" / "agents" / "helena" / "pacote"


def _pacote(texto: str) -> pda.PacoteDoAgente:
    return pda.PacoteDoAgente(
        agente="helena", versao="t", sha256="0" * 64, arquivos={"configuracao.yaml": texto}
    )


_VALIDO = """formato: helena-configuracao-v1
historico: true
v4: false
cobertura_modo: consulta
identidade_nome_operadora: Operadora Exemplo
roteador:
  modo: ativo
  modelo: mistral.magistral-small-2509
  prazo_s: 4
  max_tokens: 300
  concorrencia: 2
  conversas: [wa:amh:hk1_abc]
"""


class _Carregador:
    def __init__(self, pacote: pda.PacoteDoAgente | None) -> None:
        self.pacote = pacote

    def atual(self) -> pda.PacoteDoAgente | None:
        return self.pacote


def test_o_pacote_publicado_do_repositorio_e_valido() -> None:
    _, pacote = pda.montar("helena", "repo", _SPEC)
    dados = hp.configuracao(pacote)
    assert dados["formato"] == hp.FORMATO_CONFIGURACAO
    assert dados["roteador"]["modo"] in {"desligado", "sombra", "ativo"}


@pytest.mark.parametrize(
    ("texto", "motivo"),
    [
        ("formato: helena-configuracao-v1\nchave_nova: 1\n", "desconhecidas"),
        ("formato: outro\n", "formato"),
        ("formato: helena-configuracao-v1\nv4: sim\n", "booleano"),
        ("formato: helena-configuracao-v1\ncobertura_modo: total\n", "cobertura_modo"),
        ("formato: helena-configuracao-v1\nroteador: {modo: talvez}\n", "roteador.modo"),
        ("formato: helena-configuracao-v1\nroteador: {modo: ativo, prazo_s: 99}\n", "prazo_s"),
        ("formato: helena-configuracao-v1\nroteador: {modo: ativo, conversas: ['5511999']}\n", "conversas"),
        ("- lista\n", "mapeamento"),
    ],
)
def test_validacao_estrita_recusa_o_pacote_inteiro(texto: str, motivo: str) -> None:
    with pytest.raises(pda.PacoteInvalidoError, match=motivo):
        hp.validar(_pacote(texto))


def test_sem_pacote_a_configuracao_da_plataforma_passa_intacta_sem_a_fabrica() -> None:
    config = {"helena_v4_enabled": True, "fabrica_de_inferencia": object(), "x": 1}
    saida = hp.PacoteDaHelena(None).aplicar_no_grafo(config)
    assert saida == {"helena_v4_enabled": True, "x": 1}


def test_o_pacote_vence_a_plataforma_e_escolhe_o_modelo_do_roteador() -> None:
    chamadas: list[dict[str, Any]] = []

    def fabrica(**p: Any) -> str:
        chamadas.append(p)
        return f"provedor:{p['modelo']}"

    ph = hp.PacoteDaHelena(_Carregador(_pacote(_VALIDO)))  # type: ignore[arg-type]
    config = {
        "helena_v4_enabled": True,
        "historico_enabled": False,
        "coleta_enabled": False,
        "cobertura_modo": "simples",
        "helena_roteador": "desligado",
        "roteador_inference": None,
        "fabrica_de_inferencia": fabrica,
    }
    saida = ph.aplicar_no_grafo(config)
    assert saida["helena_v4_enabled"] is False
    assert saida["historico_enabled"] is True and saida["coleta_enabled"] is True
    assert saida["cobertura_modo"] == "simples", "o cobertura_modo de um pacote antigo e' ignorado (DL-0154)"
    assert saida["identidade_nome_operadora"] == "Operadora Exemplo"
    assert saida["helena_roteador"] == "ativo"
    assert saida["helena_roteador_conversas"] == frozenset({"wa:amh:hk1_abc"})
    assert saida["roteador_inference"] == "provedor:mistral.magistral-small-2509"
    assert chamadas == [
        {"modelo": "mistral.magistral-small-2509", "prazo_s": 4.0, "max_tokens": 300, "concorrencia": 2}
    ]
    ph.aplicar_no_grafo(config)
    assert len(chamadas) == 1, "o provedor e' reaproveitado entre turnos (pool proprio)"
    assert "fabrica_de_inferencia" not in saida


def test_roteador_desligado_no_pacote_desliga_o_provedor() -> None:
    texto = "formato: helena-configuracao-v1\nroteador: {modo: desligado}\n"
    ph = hp.PacoteDaHelena(_Carregador(_pacote(texto)))  # type: ignore[arg-type]
    saida = ph.aplicar_no_grafo(
        {"roteador_inference": "da-plataforma", "fabrica_de_inferencia": lambda **_: "x"}
    )
    assert saida["helena_roteador"] == "desligado" and saida["roteador_inference"] is None


def test_nos_fora_do_grafo_recebem_so_as_chaves_que_ja_aceitam() -> None:
    ph = hp.PacoteDaHelena(_Carregador(_pacote(_VALIDO)))  # type: ignore[arg-type]
    saida = ph.aplicar_nos_nos({"helena_v4_enabled": True, "historico_enabled": False, "inference": "i"})
    assert saida == {"helena_v4_enabled": False, "historico_enabled": True, "inference": "i"}


def test_o_adaptador_da_helena_aplica_o_pacote_ao_construir_o_grafo(monkeypatch: pytest.MonkeyPatch) -> None:
    capturado: dict[str, Any] = {}
    monkeypatch.setattr(graph, "build", lambda cfg: capturado.update(cfg) or "grafo")
    entrada = conversa.HelenaDeEntrada()
    entrada._pacote_da_helena = hp.PacoteDaHelena(_Carregador(_pacote(_VALIDO)))  # type: ignore[arg-type]
    assert entrada.construir_grafo({"helena_v4_enabled": True, "fabrica_de_inferencia": None}) == "grafo"
    assert capturado["helena_v4_enabled"] is False and "fabrica_de_inferencia" not in capturado


# --- Textos do pacote (ADR-0067 §4; etapa 5 do Plano de ajustes v2) -----------------------------------------


def _com_textos(textos_yaml: str) -> pda.PacoteDoAgente:
    return pda.PacoteDoAgente(
        agente="helena",
        versao="t",
        sha256="0" * 64,
        arquivos={"configuracao.yaml": _VALIDO, "textos.yaml": textos_yaml},
    )


def test_os_textos_do_repositorio_sao_validos_e_so_chaves_conhecidas() -> None:
    from maezo.agents.helena import textos as ht

    _, pacote = pda.montar("helena", "repo", _SPEC)
    textos = hp.textos(pacote)
    assert textos, "o pacote do repositorio traz textos.yaml"
    assert set(textos) <= set(ht.PADRAO)
    assert textos["cobertura_fora"] == ht.PADRAO["cobertura_fora"]


@pytest.mark.parametrize(
    ("texto", "motivo"),
    [
        ("formato: helena-textos-v1\ntextos: {nova_chave: oi}\n", "desconhecidas"),
        ("formato: outro\ntextos: {}\n", "formato"),
        ("formato: helena-textos-v1\ntextos: {cobertura_fora: ''}\n", "nao vazio"),
        (
            "formato: helena-textos-v1\ntextos: {cobertura_fora: 'Veja https://exemplo.com.br/x'}\n",
            "sem link",
        ),
        ("formato: helena-textos-v1\ntextos: {cobertura_fora: 'Veja em www.exemplo.org'}\n", "sem link"),
        ("formato: helena-textos-v1\ntextos: {cobertura_fora: 'Veja no gov.br'}\n", "sem link"),
        ("formato: helena-textos-v1\ntextos: {cobertura_fora: 'Oi {nome}'}\n", "marcadores"),
        ("formato: helena-textos-v1\n", "formato"),
    ],
)
def test_texto_invalido_recusa_o_pacote_inteiro(texto: str, motivo: str) -> None:
    with pytest.raises(pda.PacoteInvalidoError, match=motivo):
        hp.validar(_com_textos(texto))


def test_os_textos_do_pacote_chegam_ao_grafo() -> None:
    aviso = "formato: helena-textos-v1\ntextos: {cobertura_fora: 'Aviso novo de cobertura.'}\n"
    ph = hp.PacoteDaHelena(_Carregador(_com_textos(aviso)))  # type: ignore[arg-type]
    saida = ph.aplicar_no_grafo({"helena_roteador": "desligado"})
    assert saida["textos_do_pacote"] == {"cobertura_fora": "Aviso novo de cobertura."}


def test_sem_textos_no_pacote_vale_o_padrao_do_codigo() -> None:
    from maezo.agents.helena.textos import PADRAO, TextosDaHelena

    assert TextosDaHelena(None)["cobertura_fora"] == PADRAO["cobertura_fora"]
    assert TextosDaHelena({"cobertura_fora": "Outro."})["cobertura_fora"] == "Outro."
    assert TextosDaHelena({"chave_estranha": "x"})["cobertura_fora"] == PADRAO["cobertura_fora"]
