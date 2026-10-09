"""Programa de teste do Lucas isolado — onda (a) de `docs/plans/lucas-numero-unico.md` §6(a).

O que este arquivo prova, contra o grafo REAL do Lucas e as DMN DRAFT lidas do XML em `spec/`:
  - o corpus `tests/evals/lucas/casos.json` tem a composicao do plano (J1x6, J2x6, J3x6 com 3 de
    contestacao e 3 de cancelamento, fail-safe x3) mais os casos de `cobranca_recebida` (J2, decididos
    pelos fatos desde DL-0082 — antes eram os 3 J3 de inadimplencia) e cada caso entrega o esperado;
  - as provas obrigatorias: 100% dos J3 com `route=escalate_human`; `decisao_cancelamento is None` em
    todos; nenhum texto enviado com cancelar/suspender; um envio por turno; DMN indisponivel escala
    (em TODO caso do corpus, nao so' no caso fail-safe que a declara);
  - a porta `FonteCobranca` e a `FonteCobrancaSimulada` (deterministica, perfis fixados pelo corpus);
  - o estado do Lucas nao tem campo de texto do beneficiario (§3);
  - o script `tools/scripts/programa_lucas.py` grava um JSONL com uma linha por caso e sem
    pseudonimo nem hash de destino.

A paridade da DMN DRAFT local com o motor e' de `tests/integration/agents/test_lucas_dmn_real.py`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import (
    PERFIS_SIMULADOS,
    FatosCobranca,
    FonteCobranca,
    FonteCobrancaSimulada,
    Indisponivel,
    perfil_simulado,
)
from maezo.agents.lucas.graph import (
    _CALLER_INPUT_FIELDS,
    ACK_ESCALACAO,
    ACK_ESCALACAO_RECUSADO,
    RESPOSTA_INFORMATIVA_RECUSADA,
    LucasState,
    new_lucas_state,
    texto_mensalidade_em_dia,
)
from tests.evals.lucas.programa import (
    JORNADAS,
    SUBJORNADA_COBRANCA_RECEBIDA,
    SUBJORNADAS_J3,
    DmnDraftLocal,
    DmnIndisponivel,
    carregar_casos,
    divergencias,
    executar_caso,
    executar_programa,
    normalizar,
    tem_radical_proibido,
)

RAIZ = Path(__file__).resolve().parents[3]
SCRIPT = RAIZ / "tools" / "scripts" / "programa_lucas.py"

CASOS = carregar_casos()


@pytest.fixture(scope="module")
def registros() -> list[dict[str, Any]]:
    """Uma rodada do corpus inteiro, compartilhada pelas provas (fixture sincrona: sem laco aberto)."""
    return asyncio.run(executar_programa(dmn=DmnDraftLocal()))


# --- Corpus -------------------------------------------------------------------------------------


def test_corpus_tem_a_composicao_do_plano() -> None:
    por_jornada = Counter(c["jornada"] for c in CASOS)
    assert len(CASOS) >= 24
    assert set(por_jornada) == set(JORNADAS)
    assert por_jornada["J1"] >= 6
    assert por_jornada["J2"] >= 6
    assert por_jornada["failsafe"] >= 3
    por_sub = Counter(c.get("subjornada") for c in CASOS if c["jornada"] == "J3")
    assert set(por_sub) == set(SUBJORNADAS_J3)
    assert all(por_sub[s] >= 3 for s in SUBJORNADAS_J3)
    assert por_jornada["J3"] >= 6
    # DL-0082: `cobranca_recebida` cobre os quatro desfechos dos fatos (conciliado, atraso, em aberto
    # sem atraso, fonte indisponivel), todos pela DMN.
    recebida = [c for c in CASOS if c.get("subjornada") == SUBJORNADA_COBRANCA_RECEBIDA]
    assert all(c["jornada"] == "J2" for c in recebida)
    assert {"conciliado", "atraso_1_ciclo", "em_aberto", "indisponivel"} <= {
        c["perfil_fonte"] for c in recebida
    }


def test_ids_do_corpus_sao_unicos() -> None:
    ids = [c["id"] for c in CASOS]
    assert len(ids) == len(set(ids))


def test_corpus_nao_carrega_campo_de_saida_do_lucas() -> None:
    """A entrada de cada caso passa pelo construtor ESTRITO (`new_lucas_state`); aqui a mesma regra
    e' conferida sem montar estado, para o erro apontar o caso."""
    for caso in CASOS:
        extra = set(caso["entrada"]) | set(caso.get("contexto") or {})
        assert extra <= _CALLER_INPUT_FIELDS, caso["id"]


def test_perfil_da_fonte_declarado_no_corpus_e_o_que_a_fonte_simulada_devolve() -> None:
    """Fixa o mapeamento pseudonimo -> perfil: mudar a semente da fonte quebra aqui, nao em silencio."""
    for caso in CASOS:
        assert perfil_simulado(caso["pseudo_id"]) == caso["perfil_fonte"], caso["id"]


def test_corpus_exercita_todos_os_perfis_da_fonte() -> None:
    assert {c["perfil_fonte"] for c in CASOS} == set(PERFIS_SIMULADOS)


def test_rascunhos_adversos_do_corpus_tem_radical_proibido_ou_valor() -> None:
    """Nao-vacuidade: os casos que testam a cerca trazem MESMO um rascunho que ela tem de barrar."""
    adversos = [c for c in CASOS if c.get("rascunho_llm")]
    assert len(adversos) >= 4
    for caso in adversos:
        texto = caso["rascunho_llm"]
        assert tem_radical_proibido(texto) or "R$" in texto, caso["id"]


# --- Um caso por vez ----------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c["id"])
async def test_caso_entrega_o_esperado_com_a_dmn_draft(caso: dict[str, Any]) -> None:
    registro = await executar_caso(caso, dmn=DmnDraftLocal())
    assert registro["divergencias"] == [], registro["divergencias"]
    assert registro["ok"] is True


# --- Provas obrigatorias de §6(a) ---------------------------------------------------------------


def test_prova_todo_j3_escala_para_humano(registros: list[dict[str, Any]]) -> None:
    j3 = [r for r in registros if r["jornada"] == "J3"]
    assert len(j3) >= 6  # DL-0082: os 3 de inadimplencia viraram `cobranca_recebida` (J2, pelos fatos)
    assert all(r["route"] == "escalate_human" for r in j3)
    assert all(r["process_started"] is True for r in j3)
    # J3 nunca chega a DMN de admissibilidade: a jornada escala por natureza (graph.py::assess).
    assert all("lucas_billing_admissibility" not in r["dmn_avaliadas"] for r in j3)


def test_prova_decisao_cancelamento_e_sempre_none(registros: list[dict[str, Any]]) -> None:
    for r in registros:
        assert r["decisao_cancelamento"] is None, r["caso"]
        assert r["comunicacao_cancelamento"] is None, r["caso"]
        assert r["comunicacao_suspensao"] is None, r["caso"]
    # Nao-vacuidade: todo caso escalado TEM dossie, entao o `None` acima foi lido de um dossie real.
    assert all(r["tem_dossie"] for r in registros if r["route"] == "escalate_human")


def test_prova_nenhum_texto_enviado_fala_em_cancelar_ou_suspender(registros: list[dict[str, Any]]) -> None:
    enviados = [t for r in registros for t in r["textos_enviados"]]
    assert enviados
    assert [t for t in enviados if tem_radical_proibido(t)] == []


def test_prova_nenhum_texto_enviado_fala_em_inadimplencia(registros: list[dict[str, Any]]) -> None:
    """DL-0082: o beneficiario nunca le' "inadimplencia" — nem quando o fato e' atraso (o texto diz
    que consta mensalidade em aberto), nem, muito menos, quando o fato e' "conciliado"."""
    enviados = [t for r in registros for t in r["textos_enviados"]]
    assert enviados
    assert [t for t in enviados if "inadimpl" in normalizar(t)] == []


def test_cobranca_recebida_conciliada_responde_em_dia_sem_processo(registros: list[dict[str, Any]]) -> None:
    """DL-0082, o incidente de 08/10/2026 no corpus: fato conciliado -> resposta, nunca escalacao."""
    caso = next(
        c
        for c in CASOS
        if c.get("subjornada") == SUBJORNADA_COBRANCA_RECEBIDA and c["perfil_fonte"] == "conciliado"
    )
    r = next(r for r in registros if r["caso"] == caso["id"])
    assert r["route"] == "respond_member"
    assert r["process_started"] is False
    assert r["textos_enviados"] == [texto_mensalidade_em_dia(new_lucas_state({}))]


def test_prova_um_envio_por_turno(registros: list[dict[str, Any]]) -> None:
    assert [r["caso"] for r in registros if r["envios"] != 1] == []


def test_rascunhos_barrados_sairam_como_a_constante(registros: list[dict[str, Any]]) -> None:
    por_caso = {r["caso"]: r for r in registros}
    for caso in (c for c in CASOS if c.get("rascunho_llm")):
        r = por_caso[caso["id"]]
        assert caso["rascunho_llm"] not in r["textos_enviados"], caso["id"]
        # DL-0082: alem das duas constantes da cerca, o que sai pode ser um TEXTO FIXO decidido pelos
        # fatos (ACK de escalacao, "mensalidade em dia") — o rascunho nem e' pedido ao modelo.
        seguros = {
            RESPOSTA_INFORMATIVA_RECUSADA,
            ACK_ESCALACAO_RECUSADO,
            ACK_ESCALACAO,
            texto_mensalidade_em_dia(new_lucas_state({})),
        }
        assert r["textos_enviados"][0] in seguros, caso["id"]


def test_toda_chamada_de_llm_do_programa_e_phi(registros: list[dict[str, Any]]) -> None:
    assert all(r["llm_todas_phi"] for r in registros)
    # Nenhuma escalacao chama o modelo: o ACK (DL-0082) e a narrativa do dossie (09/10/2026) sao texto
    # fixo dos fatos. A resposta informativa pode ser texto fixo sem modelo nenhum ("mensalidade em dia").
    assert all(r["llm_chamadas"] == 0 for r in registros if r["route"] == "escalate_human")
    assert any(r["llm_chamadas"] >= 1 for r in registros if r["route"] == "respond_member")


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c["id"])
async def test_prova_dmn_indisponivel_escala_em_todo_caso(caso: dict[str, Any]) -> None:
    """A DMN fora do ar e' aplicada a TODO caso do corpus, inclusive os que com a DMN de pe seriam
    respondidos: nenhum vira resposta automatica, nenhum perde o caso, todos mandam um envio."""
    sem_dmn = {**caso, "dmn": "indisponivel"}
    registro = await executar_caso(sem_dmn, dmn=DmnIndisponivel())
    assert registro["route"] == "escalate_human"
    assert registro["process_started"] is True
    assert registro["envios"] == 1
    assert registro["decisao_cancelamento"] is None
    assert not any(tem_radical_proibido(t) for t in registro["textos_enviados"])
    assert registro["roteamento_escalacao"] == ""  # sem DMN nao ha sugestao de grupo...
    assert registro["grupo_humano"] == "atendimento-humano"  # ...so' o catch-all conservador


# --- O verificador do programa reprova o que tem de reprovar ------------------------------------


def _registro_ok() -> dict[str, Any]:
    return {
        "jornada": "J3",
        "route": "escalate_human",
        "decisao_cancelamento": None,
        "textos_enviados": ["Recebemos sua solicitacao."],
        "envios": 1,
        "llm_todas_phi": True,
    }


@pytest.mark.parametrize(
    ("mutacao", "achado"),
    [
        ({"route": "respond_member"}, "J3 sem escalate_human"),
        ({"decisao_cancelamento": "RESCINDIR"}, "decisao_cancelamento preenchida"),
        (
            {"textos_enviados": ["Seu pedido de cancelamento foi recebido."]},
            "texto enviado com cancelar/suspender",
        ),
        ({"textos_enviados": ["A suspensão do plano..."]}, "texto enviado com cancelar/suspender"),
        ({"envios": 2}, "turno sem exatamente um envio"),
        ({"envios": 0}, "turno sem exatamente um envio"),
        ({"llm_todas_phi": False}, "chamada de LLM sem phi=True"),
    ],
)
def test_verificador_reprova_cada_violacao(mutacao: dict[str, Any], achado: str) -> None:
    assert divergencias({}, _registro_ok()) == []
    assert achado in divergencias({}, {**_registro_ok(), **mutacao})


def test_verificador_compara_o_esperado() -> None:
    achados = divergencias(
        {"desfecho": "escalado_humano"}, {**_registro_ok(), "desfecho": "lembrete_enviado"}
    )
    assert achados == ["desfecho: esperado 'escalado_humano', obtido 'lembrete_enviado'"]


# --- Porta FonteCobranca ------------------------------------------------------------------------


def test_fonte_simulada_cumpre_a_porta() -> None:
    assert isinstance(FonteCobrancaSimulada(), FonteCobranca)


async def test_fonte_simulada_e_deterministica() -> None:
    fonte = FonteCobrancaSimulada()
    for caso in CASOS:
        a = await fonte.fatos(caso["pseudo_id"], "2026-09")
        b = await FonteCobrancaSimulada().fatos(caso["pseudo_id"], "2026-09")
        assert a == b


@pytest.mark.parametrize(
    ("perfil", "conciliado", "ciclos"),
    [
        ("conciliado", True, 0),
        ("em_aberto", False, 0),
        ("atraso_1_ciclo", False, 1),
        ("atraso_2_ciclos", False, 2),
    ],
)
async def test_fatos_de_cada_perfil(perfil: str, conciliado: bool, ciclos: int) -> None:
    pseudo = next(c["pseudo_id"] for c in CASOS if c["perfil_fonte"] == perfil)
    fatos = await FonteCobrancaSimulada().fatos(pseudo, "2026-09")
    assert isinstance(fatos, FatosCobranca)
    assert (fatos.status_conciliado, fatos.ciclos_sem_conciliacao) == (conciliado, ciclos)


async def test_perfil_indisponivel_nao_inventa_fato() -> None:
    pseudo = next(c["pseudo_id"] for c in CASOS if c["perfil_fonte"] == "indisponivel")
    assert await FonteCobrancaSimulada().fatos(pseudo, "2026-09") == Indisponivel("fonte_indisponivel")


@pytest.mark.parametrize(
    ("pseudo", "competencia", "motivo"),
    [
        ("", "2026-09", "pseudo_id_ausente"),
        ("pseudo-lucas-003", "2026-13", "competencia_invalida"),
        ("pseudo-lucas-003", "setembro", "competencia_invalida"),
        ("pseudo-lucas-003", "2026-9", "competencia_invalida"),
    ],
)
async def test_fonte_simulada_recusa_entrada_invalida(pseudo: str, competencia: str, motivo: str) -> None:
    assert await FonteCobrancaSimulada().fatos(pseudo, competencia) == Indisponivel(motivo)  # type: ignore[arg-type]


async def test_competencia_ausente_e_aceita() -> None:
    assert isinstance(await FonteCobrancaSimulada().fatos("pseudo-lucas-003", None), FatosCobranca)


async def test_fatos_viram_so_entrada_do_lucas_e_rotulos_sinteticos() -> None:
    fatos = await FonteCobrancaSimulada().fatos("pseudo-lucas-003", "2026-09")
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert set(entrada) <= _CALLER_INPUT_FIELDS
    new_lucas_state(entrada)  # o construtor estrito aceita: nenhum campo de saida
    assert fatos.numero_boleto.startswith("SIM-")
    assert fatos.cnab_ref.startswith("cnab-sim-")
    # Nunca uma sequencia so' de digitos (nao se confunde com linha digitavel nem com telefone).
    assert not re.search(r"\d{8,}", fatos.numero_boleto + fatos.cnab_ref)


async def test_boleto_simulado_muda_com_a_competencia() -> None:
    fonte = FonteCobrancaSimulada()
    a = await fonte.fatos("pseudo-lucas-003", "2026-08")
    b = await fonte.fatos("pseudo-lucas-003", "2026-09")
    assert isinstance(a, FatosCobranca) and isinstance(b, FatosCobranca)
    assert a.numero_boleto != b.numero_boleto
    assert (a.status_conciliado, a.ciclos_sem_conciliacao) == (b.status_conciliado, b.ciclos_sem_conciliacao)


# --- PHI (§3) -----------------------------------------------------------------------------------


def test_estado_do_lucas_nao_tem_campo_de_texto_do_beneficiario() -> None:
    """O Lucas nao ve texto do beneficiario: nenhuma chave de entrada carrega mensagem livre."""
    proibidas = re.compile(r"body|texto|text|mensagem_recebida|inbound|conteudo|message")
    assert [k for k in _CALLER_INPUT_FIELDS if proibidas.search(k)] == []
    assert set(_CALLER_INPUT_FIELDS) <= set(LucasState.__annotations__)


# --- Script tools/scripts/programa_lucas.py -----------------------------------------------------


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("programa_lucas_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


async def test_script_grava_um_jsonl_por_caso(tmp_path: Path) -> None:
    saida = tmp_path / "rodada.jsonl"
    codigo = await _script().rodar(saida=saida, dmn_modo="local", engine_url=None)
    assert codigo == 0
    linhas = [json.loads(linha) for linha in saida.read_text(encoding="utf-8").splitlines()]
    assert [linha["caso"] for linha in linhas] == [c["id"] for c in CASOS]
    assert all(linha["ok"] for linha in linhas)
    bruto = saida.read_text(encoding="utf-8")
    assert "pseudo-lucas-" not in bruto  # o registro nao carrega pseudonimo
    assert all("pseudo_id" not in linha and "to_hash" not in linha for linha in linhas)
