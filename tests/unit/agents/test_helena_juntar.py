"""ESPERAR E JUNTAR (Plano de ajustes da Helena v2, etapa 9; DL-0154).

- tres mensagens seguidas -> UMA resposta, com as tres na ordem;
- mensagem nova enquanto a resposta e' calculada -> a resposta velha nao sai e a nova refaz com tudo, a
  partir do checkpoint de ANTES;
- sinal claro de emergencia -> sem espera;
- os sinais lexicos somam no lote; fora da lista do pacote, nada muda.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maezo.agents.helena import juntar as j
from maezo.agents.helena import pacote as p
from maezo.agents.helena.graph import mensagem_urgente

_CONVERSA = "wa:amh:hk1_" + "c" * 64
_ESPERA = 0.05


class _Remetente:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str, **_: Any) -> dict[str, Any]:
        self.enviados.append(text)
        return {}


class _Compilado:
    """O grafo compilado falso: responde ecoando a mensagem e envia pelo remetente (como o `respond`)."""

    def __init__(self, remetente: Any, *, demora_s: float = 0.0) -> None:
        self.remetente = remetente
        self.demora_s = demora_s
        self.chamadas: list[tuple[dict[str, Any], Any]] = []

    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(
            config={"configurable": {**config["configurable"], "checkpoint_id": "cp-antes"}}
        )

    async def ainvoke(self, state: Any, config: Any = None) -> dict[str, Any]:
        self.chamadas.append((dict(state), config))
        await asyncio.sleep(self.demora_s)
        try:
            await self.remetente.send("h", f"resposta: {state['message_body']}")
        except j.RespostaSuperadaError:
            return {"error": "superada"}
        return {"response_text": state["message_body"]}


class _Costura:
    """A costura que roda o grafo (o despachante ou o servico do agente), com o turno do agente por cima."""

    def __init__(
        self, compilado: _Compilado, juntador: Any, espera_s: float, conversas: frozenset[str]
    ) -> None:
        self.compilado = compilado
        self.juntador = juntador
        self.espera_s = espera_s
        self.conversas = conversas

    async def ainvoke(self, estado: dict[str, Any], config: Any) -> Any:
        async with j.turno_juntado(
            estado,
            config,
            self.compilado,
            espera_s=self.espera_s,
            conversas=self.conversas,
            urgente=mensagem_urgente,
            juntador=self.juntador,
        ) as turno:
            return turno.juntado or turno.concluir(await self.compilado.ainvoke(turno.estado, turno.config))


def _montar(
    *, demora_s: float = 0.0, conversas: frozenset[str] = frozenset({_CONVERSA}), espera_s: float = _ESPERA
) -> tuple[Any, Any, Any]:
    juntador = j.Juntador()
    remetente = _Remetente()
    compilado = _Compilado(j.remetente_que_junta(remetente, juntador), demora_s=demora_s)
    return _Costura(compilado, juntador, espera_s, conversas), compilado, remetente


def _estado(texto: str, **extra: Any) -> dict[str, Any]:
    return {"conversation_id": _CONVERSA, "message_body": texto, **extra}


_CONFIG = {"configurable": {"thread_id": _CONVERSA, "checkpoint_ns": ""}}


@pytest.mark.parametrize(
    "mensagens",
    [
        ("oi", "minha filha tá com tosse", "e o boleto desse mês?"),
        ("bom dia", "queria saber da carência", "do parto"),
        ("então", "eu queria falar com alguém", "sobre a mensalidade"),
    ],
)
async def test_tres_mensagens_seguidas_recebem_uma_resposta_com_todas(mensagens: tuple[str, ...]) -> None:
    compilado_que_junta, compilado, remetente = _montar()

    async def mandar(i: int, texto: str) -> Any:
        await asyncio.sleep(i * 0.01)
        return await compilado_que_junta.ainvoke(_estado(texto), _CONFIG)

    resultados = await asyncio.gather(*(mandar(i, t) for i, t in enumerate(mensagens)))
    assert [r.get(j.CHAVE_JUNTADA) for r in resultados] == [True, True, None]
    assert len(compilado.chamadas) == 1 and len(remetente.enviados) == 1
    assert compilado.chamadas[0][0]["message_body"] == "\n".join(mensagens)


async def test_mensagem_nova_durante_a_resposta_barra_o_envio_e_refaz_do_checkpoint_de_antes() -> None:
    compilado_que_junta, compilado, remetente = _montar(demora_s=0.1)
    primeira = asyncio.ensure_future(compilado_que_junta.ainvoke(_estado("tô com dor de cabeça"), _CONFIG))
    await asyncio.sleep(_ESPERA + 0.03)  # a primeira ja' passou da espera e esta' calculando
    segunda = await compilado_que_junta.ainvoke(_estado("e também enjoo"), _CONFIG)
    r1 = await primeira
    assert r1.get(j.CHAVE_JUNTADA) is True and r1.get("refeita") is True
    assert remetente.enviados == ["resposta: tô com dor de cabeça\ne também enjoo"]
    estado, config = compilado.chamadas[-1]
    assert estado["message_body"] == "tô com dor de cabeça\ne também enjoo"
    assert config["configurable"]["checkpoint_id"] == "cp-antes", "o turno refeito parte de ANTES do barrado"
    assert segunda == {"response_text": "tô com dor de cabeça\ne também enjoo"}


@pytest.mark.parametrize(
    "alarme", ["a bolsa estourou", "estou com dor no peito e falta de ar", "quero me matar"]
)
async def test_sinal_claro_de_emergencia_nao_espera(alarme: str) -> None:
    costura, _compilado, remetente = _montar(espera_s=5.0)
    await asyncio.wait_for(costura.ainvoke(_estado(alarme), _CONFIG), timeout=1.0)
    assert len(remetente.enviados) == 1


async def test_os_sinais_lexicos_somam_no_lote() -> None:
    compilado_que_junta, compilado, _ = _montar()
    a = asyncio.ensure_future(
        compilado_que_junta.ainvoke(_estado("minha mãe tá com febre", sinal_saude_lexico=True), _CONFIG)
    )
    await asyncio.sleep(0.01)
    await compilado_que_junta.ainvoke(_estado("e o boleto?", sinal_saude_lexico=False), _CONFIG)
    await a
    assert compilado.chamadas[-1][0]["sinal_saude_lexico"] is True


async def test_fora_da_lista_nada_muda() -> None:
    compilado_que_junta, compilado, remetente = _montar(conversas=frozenset())
    await compilado_que_junta.ainvoke(_estado("oi"), _CONFIG)
    assert len(compilado.chamadas) == 1 and remetente.enviados == ["resposta: oi"]
    assert compilado.chamadas[0][1] == _CONFIG


def test_o_pacote_valida_o_juntar_mensagens() -> None:
    assert p._erros_do_juntar({"espera_s": 4.0, "conversas": [_CONVERSA]}) == []
    assert p._erros_do_juntar({"espera_s": 40, "conversas": [_CONVERSA]})
    assert p._erros_do_juntar({"espera_s": 4.0, "conversas": ["5511999999999"]})
    assert p._erros_do_juntar({"espera": 4.0})


def test_dl_0154_espera_de_juntar_e_2_5_s_e_consulta_do_plano_ate_10_s() -> None:
    """DL-0154 (adendo de 24/10/2026, latencia e alarme), itens 4 e 5: `juntar_mensagens.espera_s` cai de 4
    para 2,5 s no pacote e as consultas do plano (TINA) esperam ate' 10 s (dependentes = duas consultas
    Athena em sequencia). Mudar um destes valores e' mudar a decisao registrada: exige novo adendo na DL
    (R-115)."""
    from pathlib import Path

    import yaml

    from maezo.agents.helena import graph
    from maezo.agents.helena.habilidades.plano import consulta

    raiz = Path(__file__).parents[3]
    dados = yaml.safe_load((raiz / "spec/agents/helena/pacote/configuracao.yaml").read_text(encoding="utf-8"))
    juntar = dados["juntar_mensagens"]
    assert juntar["espera_s"] == 2.5
    assert p._erros_do_juntar(juntar) == []
    assert graph.CONSULTA_PRAZO_S == 10.0
    assert consulta.CONSULTA_PRAZO_S == 10.0, "o valor que a consulta do plano usa de fato"
