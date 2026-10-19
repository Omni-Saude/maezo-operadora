"""A bateria do Lucas (`maezo.agents.lucas.bateria`) rodada OFFLINE no grafo real.

O que e' real aqui: o grafo do Lucas e as DUAS tabelas de decisao (avaliadas a partir dos `.dmn`
vivos, nao de linhas fixas). O que e' falso: modelo, WhatsApp, motor e auditoria — so' o que o
ambiente de teste nao pode ter. O objetivo e' provar que as HIPOTESES dos casos batem com as
tabelas ANTES de gastar uma execucao no ambiente de desenvolvimento.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.bateria import (
    CHAVES_DO_GRAFO,
    GravadorDeEnvios,
    chaves_do_caso,
    executar,
    executar_caso,
    veredito,
)
from maezo.agents.lucas.bateria_casos import CASOS
from maezo.agents.lucas.graph import LucasGraph
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table


class _DmnVivo:
    """Avalia as tabelas `.dmn` reais (primeiro acerto), no formato que o grafo espera."""

    def __init__(self) -> None:
        self._tabelas = {
            chave: read_live_table(DMN_DIR / f"{chave}.dmn")
            for chave in ("lucas_billing_admissibility", "lucas_escalation_routing")
        }

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        key = decision_key
        tabela = self._tabelas.get(key)
        if tabela is None:
            raise DmnEvaluationError(f"tabela desconhecida: {key}")
        veredito_ = evaluate(tabela, {nome: variables[nome] for nome in tabela.input_names})
        return [veredito_.saidas], DmnVersion(
            key=key, id=f"{key}:1:offline", version=1, deployment_id="offline"
        )


class _Modelo:
    async def generate(self, prompt: str, **_: Any) -> str:
        return "Recebemos sua mensagem e vamos te ajudar com as informacoes da sua cobranca."


class _Zap:
    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        return {"suppressed_synthetic": True}


def _grafo(caso: dict[str, Any] | None = None) -> tuple[Any, GravadorDeEnvios]:
    """O grafo offline, com as chaves que o CASO liga (o mesmo repasse da bateria real)."""
    gravador = GravadorDeEnvios(_Zap())
    grafo = LucasGraph(
        inference=_Modelo(),  # type: ignore[arg-type]
        dmn=_DmnVivo(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=gravador,  # type: ignore[arg-type]
        **chaves_do_caso(caso or {}),
    )
    return grafo.compile_graph().compile(), gravador


def test_ids_dos_casos_sao_unicos_e_cada_um_tem_hipotese() -> None:
    ids = [c["id"] for c in CASOS]
    assert len(ids) == len(set(ids))
    assert all(c["esperado"] for c in CASOS)


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
async def test_cada_caso_roda_no_grafo_real_sem_excecao(caso: dict[str, Any]) -> None:
    grafo, gravador = _grafo(caso)
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert "erro_da_execucao" not in resultado, resultado.get("erro_da_execucao")


#: ACHADO DE 01/10/2026 (bateria do Lucas), AGORA FECHADO: o catch-all de "ambiguidade" (L07, L09, L18)
#: era rotulado `inadimplencia_detectada` e o beneficiario lia "inadimplencia detectada" sem haver
#: indicio de atraso. A `lucas_billing_admissibility` passou a devolver `categoria` (atraso vs
#: ambiguidade) e o grafo a usa. Este conjunto ficou vazio de proposito: um caso novo que divirja
#: derruba o teste, em vez de ser engolido por uma lista de excecoes.
_ACHADOS_ABERTOS: set[str] = set()


async def test_veredito_offline_contra_as_tabelas_vivas() -> None:
    """Todo caso fecha OK contra as tabelas vivas (nenhum achado aberto)."""
    divergentes: set[str] = set()
    for indice, caso in enumerate(CASOS):
        grafo, gravador = _grafo(caso)
        resultado = await executar_caso(grafo, gravador, caso, indice=indice, execucao="offline")
        status, div, obs = veredito(caso, resultado)
        if status != "OK":
            divergentes.add(caso["id"])
            assert status == "DIVERGE", (caso["id"], obs)
    assert divergentes == _ACHADOS_ABERTOS


def test_r_cifrao_so_e_aceito_nos_casos_de_valor() -> None:
    """DL-0086: nos casos `valores_permitidos` o R$ vem dos fatos (a cerca do grafo confere); nos
    demais ele continua apontado como divergencia."""
    resultado = {
        "rejeitado": False,
        "final": {
            "route": "respond_member",
            "desfecho": "resposta_informativa_enviada",
            "process_started": False,
        },
        "mensagem": "Sua mensalidade foi de R$ 8.389,53.",
        "enviados": [],
    }
    de_valor = next(c for c in CASOS if c["id"] == "L25")
    de_boleto = next(c for c in CASOS if c["id"] == "L01")
    assert veredito(de_valor, resultado)[1] == []
    assert any("r$" in d for d in veredito(de_boleto, resultado)[1])


async def test_ciclos_ilegiveis_escalam_para_humano_em_vez_de_derrubar_o_turno() -> None:
    caso = next(c for c in CASOS if c["id"] == "L24")
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert resultado["final"]["route"] == "escalate_human"
    assert resultado["final"]["motivo_humano"] == "ambiguidade"
    assert resultado["final"]["process_started"] is True


@pytest.mark.parametrize("caso_id", ["L02", "L34"])
async def test_a_segunda_via_sai_em_texto_fixo_sem_o_rascunho_do_modelo(caso_id: str) -> None:
    """10/10/2026 (teste real do dono): a 2a via e' texto fixo — o rascunho de `_Modelo` nunca sai."""
    caso = next(c for c in CASOS if c["id"] == caso_id)
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    texto = resultado["mensagem"]
    assert texto.startswith("Você consegue a 2ª via pelo aplicativo Austa Clínicas, pelo portal do plano")
    assert "Recebemos sua mensagem" not in texto and not texto.startswith("Olá")
    if caso_id == "L34":
        # O ano so' aparece quando nao e' o de hoje (DL-0087); a bateria roda com o relogio real.
        assert "A mensalidade de setembro" in texto and "já está paga." in texto


@pytest.mark.parametrize(
    ("caso_id", "esperado"),
    [
        ("L35", "você não teve coparticipação: R$ 0,00."),
        ("L36", "Você pagou a mensalidade de agosto de 2026 em 09/08"),
    ],
)
async def test_a_pergunta_de_valor_responde_so_o_campo_perguntado(caso_id: str, esperado: str) -> None:
    """DL-0087: com o foco do handoff, a resposta e' so' o campo perguntado (nada de extrato)."""
    caso = next(c for c in CASOS if c["id"] == caso_id)
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    texto = resultado["mensagem"]
    assert esperado in texto
    assert "boleto" not in texto and "saldo" not in texto


async def test_a_saida_traz_a_narrativa_do_dossie_truncada() -> None:
    """09/10/2026: a narrativa do dossie (texto fixo) vai na saida do caso, para conferir no ambiente."""
    caso = next(c for c in CASOS if c["id"] == "L24")
    grafo, gravador = _grafo()
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    narrativa = resultado["dossie_narrativa"]
    assert narrativa.startswith("Encaminhado ao atendente porque ")
    assert len(narrativa) <= 600


# --- 16/10/2026 (frente D do SP-OP-ESCALATION-001): chaves do grafo repassadas por caso ---------------


def test_toda_chave_de_caso_e_um_interruptor_que_o_grafo_le() -> None:
    """Os nomes de `CHAVES_DO_GRAFO` sao os que `lucas.graph.build` le' do `deps` (nunca um nome a mais)."""
    import inspect

    from maezo.agents.lucas import graph as lucas_graph

    fonte = inspect.getsource(lucas_graph.build)
    for nome in CHAVES_DO_GRAFO:
        assert f'cfg.get("{nome}")' in fonte, nome
    assert {nome for c in CASOS for nome in c.get("chaves", {})} <= set(CHAVES_DO_GRAFO)


def test_chave_desconhecida_no_caso_e_recusada_antes_de_rodar() -> None:
    with pytest.raises(ValueError, match="desconhecida"):
        chaves_do_caso({"id": "X", "chaves": {"cancelamento_pf_v2": True}})
    assert chaves_do_caso({"id": "Y", "chaves": {"lucas_v4": "sim"}}) == {"lucas_v4": False}  # so' True liga


async def test_executar_monta_um_grafo_por_combinacao_de_chaves() -> None:
    montados: list[dict[str, bool]] = []

    def _montar(chaves: Any) -> tuple[Any, GravadorDeEnvios]:
        montados.append(dict(chaves))
        return _grafo({"chaves": dict(chaves)})

    escolhidos = [c for c in CASOS if c["id"] in {"L01", "L39", "L02"}]
    resultados = await executar(_montar, escolhidos)
    assert montados == [{}, {"cancelamento_pf_v1": True}]
    assert [r.get("chaves") for r in resultados] == [None, None, {"cancelamento_pf_v1": True}]


@pytest.mark.parametrize(
    ("caso_id", "prefixo"),
    [("L37", "ESC-"), ("L39", "CANCEL-")],
)
async def test_o_processo_aberto_e_o_da_hipotese(caso_id: str, prefixo: str) -> None:
    """Negociacao PF abre o caso humano; cancelamento PF com a chave abre SP-OP-CANCEL-001."""
    caso = next(c for c in CASOS if c["id"] == caso_id)
    grafo, gravador = _grafo(caso)
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert resultado["final"]["process_started"] is True
    assert resultado["final"]["business_key"].startswith(prefixo)
    assert veredito(caso, resultado)[1] == []


async def test_veredito_aponta_chave_e_texto_que_nao_batem() -> None:
    caso = next(c for c in CASOS if c["id"] == "L40")
    grafo, gravador = _grafo(caso)
    resultado = await executar_caso(grafo, gravador, caso, indice=0, execucao="offline")
    assert veredito(caso, resultado)[1] == []
    sem_texto = {**resultado, "mensagem": "Outro texto.", "enviados": []}
    assert "o texto ao beneficiario nao traz o trecho fixo esperado" in veredito(caso, sem_texto)[1]
    cancel = next(c for c in CASOS if c["id"] == "L39")
    outra_chave = {**resultado, "final": {**resultado["final"], "business_key": "ESC-amh-x"}}
    assert any("CANCEL-" in d for d in veredito(cancel, outra_chave)[1])
