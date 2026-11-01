"""A PORTA UNICA da saude (DL-0160, dono 01/11/2026): o modelo so' LE, a tabela DMN decide e tudo que e'
clinico vai a equipe. As tabelas sao as REAIS de `spec/processes/dmn`, lidas pelo leitor FIRST-hit de
`tests/support/dmn_first_hit.py`. Nenhuma camada antiga de saude responde.

Turno INTEIRO do grafo (roteador ativo); modelo falso (leitura por fila); nenhuma rede. Sem dado pessoal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import roteador as rt
from maezo.agents.helena.graph import (
    RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_SAUDE,
    HelenaGraph,
)
from maezo.agents.helena.habilidades.saude_original import fluxo as so
from maezo.agents.helena.habilidades.saude_original import leitura as lt
from maezo.conhecimento import saude_gerativa as sg
from maezo.runtime.inference.experimento_global import RespostaDaRotaGlobal
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnVersion
from tests.support import dmn_first_hit as dfh
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _entrada, _WhatsApp
from tests.unit.agents.test_helena_saude_gerativa import _Conversa

_DMN = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"


class _DmnReal:
    """As tabelas DMN REAIS do repositorio (FIRST-hit), sem engine."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas.append((decision_key, dict(variables)))
        tabela = dfh.read_live_table(_DMN / f"{decision_key}.dmn")
        valores = {nome: variables.get(nome) for nome in tabela.input_names}
        versao = DmnVersion(key=decision_key, id=f"{decision_key}:1", version=1, deployment_id="teste")
        return [dfh.evaluate(tabela, valores).saidas], versao


class _Leitor:
    """A rota do modelo da allowlist, FALSA: so' o prompt da LEITURA e' esperado (qualquer outro levanta)."""

    rota = sg.ROTA_DE_RESIDENCIA if hasattr(sg, "ROTA_DE_RESIDENCIA") else "experimento"
    modelo = "global.anthropic.claude-haiku-5-5"

    def __init__(self, *leituras: dict[str, Any] | str) -> None:
        self.fila = list(leituras)
        self.chamadas = 0

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        assert sistema == lt.PROMPT_LEITURA, "so' a leitura pode chamar o modelo na porta unica"
        self.chamadas += 1
        item = self.fila.pop(0) if self.fila else "sem json"
        texto = item if isinstance(item, str) else json.dumps(item)
        return RespostaDaRotaGlobal(texto=texto, parada="end_turn", entrada=1, saida=1, latencia_ms=1)


class _Modelo:
    """O roteador (GLM) recebe as decisoes em ordem; qualquer outra chamada e' registrada."""

    def __init__(self, *decisoes: str) -> None:
        self.decisoes = list(decisoes)
        self.outras: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            return self.decisoes.pop(0) if self.decisoes else ""
        self.outras.append(prompt[:80])
        return "Resumo do caso para a equipe."


def _leitura(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "assunto": "sintoma",
        "populacao": "adult",
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "idade_anos": None,
        "idade_meses": None,
        "idade_gestacional_semanas": None,
        "risco_imediato": False,
    }
    return base | campos


def _grafo(
    modelo: _Modelo, leitor: _Leitor, *, ligada: bool = True
) -> tuple[Any, _WhatsApp, FakeCibSevenTransport, _DmnReal]:
    whatsapp, cib, dmn = _WhatsApp(), FakeCibSevenTransport(), _DmnReal()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=dmn,  # type: ignore[arg-type]
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 11, 1, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=leitor, conversas=frozenset({_CONVERSA})),  # type: ignore[arg-type]
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
        helena_saude_original_enabled=ligada,
    )
    return grafo.compile_graph().compile(), whatsapp, cib, dmn


def _casos(cib: FakeCibSevenTransport) -> list[dict[str, Any]]:
    return [cib._variables[k] for k in cib._history]


@pytest.fixture(autouse=True)
def _camadas_antigas_nunca(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Nenhuma camada antiga de saude pode responder nestas conversas (fora o teste da chave desligada)."""
    if "chave_desligada" in request.node.name:
        return

    async def proibido(*_: Any, **__: Any) -> Any:
        raise AssertionError("camada antiga de saude chamada com a porta unica ligada")

    def proibido_sincrono(*_: Any, **__: Any) -> Any:
        raise AssertionError("trava antiga de alarme chamada com a porta unica ligada")

    monkeypatch.setattr(rt, "_escalar_alarme_do_modelo", proibido)
    monkeypatch.setattr(HelenaGraph, "_sinal_claro_pelo_texto", proibido_sincrono)
    monkeypatch.setattr(rt._protocolo, "conduzir", proibido)
    monkeypatch.setattr(rt._protocolo, "responder_alerta", proibido)
    monkeypatch.setattr(rt._protocolo, "sem_roteador", proibido)
    monkeypatch.setattr(rt._protocolo, "sem_alerta_geral", proibido)


async def test_p1_da_tabela_e_o_192_e_caso_grave() -> None:
    leitor = _Leitor(_leitura(sintoma_codigo="dor_toracica", intensidade="moderada", idade_anos=50))
    grafo, whatsapp, cib, dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    await grafo.ainvoke(_entrada("estou com dor no peito desde cedo"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [c["severidade"] for c in _casos(cib)] == ["grave"]
    assert dmn.chamadas[0][0] == "triage_redflag_adult"


async def test_p2_da_tabela_e_caso_moderado_sem_ligue_192_agora() -> None:
    # A mae de 70 anos que desmaiou na semana passada: sincope = P2 (enfermagem) na tabela adulta.
    leitor = _Leitor(_leitura(sintoma_codigo="sincope", idade_anos=70))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    await grafo.ainvoke(_entrada("minha mae de 70 anos desmaiou semana passada e esta fraca"))
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE
    assert "Ligue 192 (SAMU) agora" not in whatsapp.enviados[-1]
    caso = _casos(cib)[0]
    assert (caso["motivo_categoria"], caso["severidade"]) == ("red_flag_clinico", "moderada")


async def test_sem_alarme_abre_caso_para_a_equipe_de_saude() -> None:
    leitor = _Leitor(_leitura(idade_anos=42))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    await grafo.ainvoke(_entrada("meu irmao de 42 anos esta com tosse seca ha 3 dias e perdeu o paladar"))
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE
    caso = _casos(cib)[0]
    assert (caso["motivo_categoria"], caso["severidade"]) == ("intencao_clinica", "moderada")


async def test_pergunta_clinica_vai_para_a_equipe() -> None:
    leitor = _Leitor(_leitura(assunto="pergunta_clinica"))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("saude")), leitor)
    await grafo.ainvoke(_entrada("voce nao acha que pode ser gripe ou covid?"))
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE
    assert _casos(cib)[0]["motivo_categoria"] == "intencao_clinica"


async def test_recem_nascido_com_choro_que_nao_para_e_p1_da_tabela_pediatrica() -> None:
    leitor = _Leitor(_leitura(populacao="pediatric", sintoma_codigo="choro_inconsolavel", idade_meses=0))
    grafo, whatsapp, cib, dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    await grafo.ainvoke(_entrada("meu bebe de 20 dias esta chorando sem parar ha horas"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert dmn.chamadas[0][0] == "triage_redflag_pediatric"
    assert _casos(cib)[0]["severidade"] == "grave"


async def test_umbigo_umido_sem_codigo_na_tabela_vai_a_equipe_sem_192() -> None:
    leitor = _Leitor(_leitura(populacao="pediatric", idade_meses=0))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    await grafo.ainvoke(_entrada("meu filho nasceu ha duas semanas e o umbigo esta umido com cheiro ruim"))
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE
    assert _casos(cib)[0]["motivo_categoria"] == "intencao_clinica"


async def test_risco_de_suicidio_pelo_texto_e_o_caminho_de_sempre_com_cvv() -> None:
    leitor = _Leitor()
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("saude")), leitor)
    await grafo.ainvoke(_entrada("nao aguento mais, quero morrer"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL
    assert leitor.chamadas == 0
    assert _casos(cib)[0]["motivo_categoria"] == "risco_psicossocial"


async def test_palavra_de_alarme_traz_a_mensagem_mesmo_com_o_roteador_em_outra_habilidade() -> None:
    leitor = _Leitor(_leitura(sintoma_codigo="sincope", idade_anos=40))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("conversa")), leitor)
    await grafo.ainvoke(_entrada("meu marido desmaiou agora"))
    assert leitor.chamadas == 1
    assert _casos(cib)[0]["motivo_categoria"] == "red_flag_clinico"
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE


async def test_leitura_que_falha_com_o_roteador_dizendo_sintoma_vai_a_equipe() -> None:
    leitor = _Leitor("nao sei", "de novo nao")
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("estou com uma dor estranha"))
    assert any(e.get("event") == "helena_saude_original_leitura_falhou" for e in logs)
    assert _casos(cib)[0]["motivo_categoria"] == "intencao_clinica"
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE


async def test_duvida_geral_de_saude_so_com_texto_revisado_da_base() -> None:
    leitor = _Leitor(_leitura(assunto="duvida_saude"))
    modelo = _Modelo(decisao_v7("saude"))
    grafo, whatsapp, cib, dmn = _grafo(modelo, leitor)
    await grafo.ainvoke(_entrada("o que é PSA?"))
    assert leitor.chamadas == 1  # so' a leitura: nenhum texto de saude escrito pelo modelo
    assert cib._history == {} and dmn.chamadas == []
    assert "psa" in whatsapp.enviados[-1].lower()


async def test_assunto_que_nao_e_saude_segue_o_caminho_de_sempre_sem_leitura() -> None:
    leitor = _Leitor()
    grafo, _whatsapp, cib, dmn = _grafo(_Modelo(decisao_v7("conversa")), leitor)
    await grafo.ainvoke(_entrada("valeu!"))
    assert leitor.chamadas == 0 and dmn.chamadas == [] and cib._history == {}


def test_a_chave_vem_desligada_e_o_pacote_liga() -> None:
    from maezo.agents.helena import pacote as pc

    saida: dict[str, Any] = {}
    pc.PacoteDaHelena._aplicar_booleanas(saida, {so.CHAVE_DO_PACOTE: True})
    assert saida == {"helena_saude_original_enabled": True}


def test_a_leitura_so_aceita_codigo_da_tabela_do_publico() -> None:
    lida = lt.ler_json(json.dumps(_leitura(populacao="adult", sintoma_codigo="choro_inconsolavel")))
    assert lida.sintoma_codigo is None
    with pytest.raises(ValueError):
        lt.ler_json(json.dumps(_leitura(assunto="diagnostico")))


async def test_com_caso_de_saude_aberto_queixa_nova_sem_emergencia_pede_para_contar_ao_profissional() -> None:
    leitor = _Leitor(_leitura(idade_anos=42), _leitura(idade_anos=38, sintoma_codigo="febre"))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma"), decisao_v7("sintoma")), leitor)
    r1 = await grafo.ainvoke(_entrada("meu irmao esta com tosse ha 3 dias"))
    memoria = {k: r1[k] for k in ("historico_conversa",) if r1.get(k)}
    await grafo.ainvoke(
        _entrada("minha esposa de 38 anos esta com ardencia para urinar e febre baixa") | memoria
    )
    assert whatsapp.enviados[-1] == so.TEXTO_CASO_DE_SAUDE_ABERTO
    assert len(_casos(cib)) == 1


async def test_com_caso_de_saude_aberto_emergencia_nova_continua_no_192() -> None:
    leitor = _Leitor(
        _leitura(idade_anos=42),
        _leitura(populacao="pediatric", sintoma_codigo="dificuldade_respiratoria", idade_anos=6),
    )
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("sintoma"), decisao_v7("sintoma")), leitor)
    r1 = await grafo.ainvoke(_entrada("meu irmao esta com tosse ha 3 dias"))
    memoria = {k: r1[k] for k in ("historico_conversa",) if r1.get(k)}
    await grafo.ainvoke(_entrada("meu filho de 6 anos esta com muita dificuldade para respirar") | memoria)
    assert whatsapp.enviados[-1].startswith("Ligue")
    assert "grave" in [c["severidade"] for c in _casos(cib)]


@pytest.mark.parametrize(
    ("frase", "esperado"),
    [
        ("ok, agora trago outro assunto:", so.TEXTO_PODE_CONTAR),
        ("Não sei se vc reparou, mas eu já mudei o assunto.", so.TEXTO_PODE_CONTAR),
        ("O que eu te respondi pra você ter tirado essa conclusão?", so.TEXTO_CONVERSA),
    ],
)
async def test_conversa_fora_da_saude_tem_texto_fixo(frase: str, esperado: str) -> None:
    leitor = _Leitor()
    modelo = _Modelo(decisao_v7("conversa"))
    grafo, whatsapp, cib, _dmn = _grafo(modelo, leitor)
    await grafo.ainvoke(_entrada(frase))
    assert whatsapp.enviados[-1] == esperado
    assert leitor.chamadas == 0 and modelo.outras == [] and cib._history == {}


async def test_sinal_claro_de_agora_sem_leitura_e_o_192_de_sempre() -> None:
    leitor = _Leitor("nao sei", "de novo nao")
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("conversa")), leitor)
    await grafo.ainvoke(_entrada("meu filho esta convulsionando"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [c["severidade"] for c in _casos(cib)] == ["grave"]


async def test_mensagem_trazida_pelo_alarme_nunca_vira_nao_e_saude() -> None:
    leitor = _Leitor(_leitura(assunto="outro"))
    grafo, whatsapp, cib, _dmn = _grafo(_Modelo(decisao_v7("conversa")), leitor)
    await grafo.ainvoke(_entrada("meu pai desmaiou"))
    assert _casos(cib), "a mensagem com alarme abriu caso"
    assert whatsapp.enviados[-1] not in so.TEXTOS_FIXOS


async def test_chave_desligada_nao_le_e_segue_a_helena_de_antes() -> None:
    leitor = _Leitor()
    grafo, whatsapp, _cib, dmn = _grafo(_Modelo(decisao_v7("sintoma")), leitor, ligada=False)
    await grafo.ainvoke(_entrada("estou com dor no peito forte agora"))
    assert leitor.chamadas == 0
    assert "192" in whatsapp.enviados[-1]


def test_leitura_com_tipo_inesperado_e_valueerror() -> None:
    with pytest.raises(ValueError):
        lt.ler_json(json.dumps(_leitura(assunto=["sintoma"])))
    with pytest.raises(ValueError):
        lt.ler_json('{"assunto": "sintoma", "populacao": "adult", "idade_anos": Infinity}')


def test_idade_da_crianca_vai_em_meses_para_a_tabela_pediatrica() -> None:
    lida = lt.ler_json(json.dumps(_leitura(populacao="pediatric", idade_anos=3)))
    assert lida.idade_meses == 36
    assert lt.ler_json(json.dumps(_leitura(populacao="pediatric", idade_meses=14))).idade_meses == 14
