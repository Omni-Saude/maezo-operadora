"""Triagem com juiz da Helena (experimento do dev, DL-0159): a TABELA e' o piso e o MODELO julga a conversa
inteira e so' sobe a gravidade; no maximo 3 perguntas; 192 so' pela tabela. So' com a chave
`triagem_juiz: true` no pacote e na allowlist do modelo.

Modelo falso (respostas por papel do prompt); nenhuma rede. Frases de exemplo sem dado pessoal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.triagem_juiz import fluxo as tj
from maezo.agents.helena.habilidades.triagem_juiz import motor as m
from maezo.runtime.inference.experimento_global import RespostaDaRotaGlobal
from tests.unit.agents.test_helena_modelo_allowlist import _OUTRA, _estado, _Glm, _grafo

_FATOS_LEVES: dict[str, Any] = {
    "intencao": "queixa",
    "queixas": ["dor_garganta"],
    "queixa_texto": "dor de garganta",
    "paciente_quem": "propria",
    "faixa_etaria": "adulto",
    "gestacao": "nao",
    "intensidade_0_10": 3,
    "tem_dor": True,
    "condicoes": [],
    "sinais": {},
    "risco_suicidio": "nao",
}


class _Modelo:
    """Responde pelo papel do prompt de sistema (anotador, leitor, pergunta do juiz, juiz)."""

    def __init__(
        self,
        *,
        fatos: dict[str, Any] | None = None,
        pergunta: str | None = "Tem alguma doença ou usa remédio todo dia?",
        juiz: str = "CONVERSA",
        motivo: str = "queixa leve",
        leitura: str = "NAO",
        item_192: int | None = None,
    ) -> None:
        self.fatos = fatos or _FATOS_LEVES
        self.pergunta = pergunta
        self.juiz = juiz
        self.motivo = motivo
        self.leitura = leitura
        self.item_192 = item_192
        self.papeis: list[str] = []

    def papel(self, sistema: str) -> str:
        if sistema.startswith("Você é um ANOTADOR"):
            return "anotador"
        if sistema.startswith("Você lê UMA resposta"):
            return "leitor"
        if "decida se precisa fazer UMA pergunta" in sistema:
            return "pergunta"
        return "juiz"

    async def __call__(self, sistema: str, texto: str, max_tokens: int) -> str:
        papel = self.papel(sistema)
        self.papeis.append(papel)
        if papel == "anotador":
            return json.dumps(self.fatos)
        if papel == "leitor":
            # Pergunta invertida da tabela ("Consegue engolir?"): a resposta tranquila e' "sim".
            pergunta = texto.split("\n", 1)[0].removeprefix("Pergunta: ").strip()
            invertida = any(
                v.get("pergunta_inverte") and str(v.get("pergunta") or "").strip() in pergunta
                for v in m.SINAIS.values()
                if v.get("pergunta")
            )
            return "SIM" if invertida and self.leitura == "NAO" else self.leitura
        if papel == "pergunta":
            return json.dumps({"pergunta": self.pergunta})
        return json.dumps({"saida": self.juiz, "item_192": self.item_192, "motivo": self.motivo})


async def _ate_o_fim(modelo: _Modelo, mensagem: str, resposta: str = "não, nada disso") -> dict[str, Any]:
    ctx = m.contexto_novo([mensagem])
    r = await m.passo(modelo, ctx, None)
    while r["tipo"] == "pergunta":
        ctx = json.loads(json.dumps(ctx))  # o contexto atravessa o turno como texto (coleta_contexto)
        r = await m.passo(modelo, ctx, resposta)
    assert len(ctx["qa"]) <= m.MAX_PERGUNTAS
    return r


# --- O motor -----------------------------------------------------------------------------------------------
async def test_sinal_claro_no_texto_vai_ao_192_sem_modelo() -> None:
    modelo = _Modelo()
    r = await m.passo(modelo, m.contexto_novo(["meu pai nao acorda, ja chamei e nada"]), None)
    assert r["saida"] == "192"
    assert modelo.papeis == []


async def test_queixa_leve_pergunta_uma_vez_e_orienta() -> None:
    modelo = _Modelo(juiz="CONVERSA")
    r = await _ate_o_fim(modelo, "estou com dor de garganta desde ontem")
    assert r["saida"] == "CONVERSA"
    assert modelo.papeis.count("pergunta") == 1


async def test_o_juiz_so_sobe_a_gravidade() -> None:
    modelo = _Modelo(juiz="PS", motivo="suspeita de quadro grave")
    r = await _ate_o_fim(modelo, "estou com dor de garganta desde ontem")
    assert r["saida"] == "PS"
    assert r["regra"].startswith("juiz.")


async def test_o_juiz_nunca_da_192_sozinho() -> None:
    modelo = _Modelo(juiz="192", item_192=4)
    r = await _ate_o_fim(modelo, "estou com dor de garganta desde ontem")
    assert r["saida"] == "PS"


async def test_motivo_que_fala_em_pronto_socorro_nao_fica_abaixo_de_ps() -> None:
    modelo = _Modelo(juiz="HOJE", motivo="sugere apendicite, precisa de avaliacao")
    r = await _ate_o_fim(modelo, "estou com dor de garganta desde ontem")
    assert r["saida"] == "PS"


async def test_tristeza_pergunta_sobre_pensamento_de_morte() -> None:
    fatos = {**_FATOS_LEVES, "queixas": ["saude_mental"], "tem_dor": False}
    modelo = _Modelo(fatos=fatos, pergunta=None)
    ctx = m.contexto_novo(["ando muito triste e sem dormir, tem remedio pra dormir?"])
    r = await m.passo(modelo, ctx, None)
    assert r["tipo"] == "pergunta"
    assert r["texto"] == m.PERGUNTA_SUICIDIO


async def test_falha_do_modelo_vira_excecao_para_quem_chama() -> None:
    async def quebrado(sistema: str, texto: str, max_tokens: int) -> str:
        return "sem json"

    with pytest.raises(RuntimeError):
        await m.passo(quebrado, m.contexto_novo(["estou com dor de garganta"]), None)


# --- A chave e o turno da Helena --------------------------------------------------------------------------
def test_a_chave_vem_desligada_e_o_pacote_liga() -> None:
    from maezo.agents.helena import pacote as pc

    grafo = _grafo(_Glm(), _HaikuPorPapel(_Modelo()))  # type: ignore[arg-type]
    assert not tj.ligada(grafo)
    saida: dict[str, Any] = {}
    pc.PacoteDaHelena._aplicar_booleanas(saida, {tj.CHAVE_DO_PACOTE: True})
    assert saida == {"helena_triagem_juiz_enabled": True}


def _ligado(grafo: Any) -> Any:
    grafo._triagem_juiz = True  # o que o pacote `triagem_juiz: true` faz via `graph.build`
    return grafo


@dataclass(frozen=True)
class _Decisao:
    habilidade: str = "sintoma"
    provavel: str | None = None
    v7: Any = None


class _HaikuPorPapel:
    """A rota global falsa: cada prompt de sistema recebe a resposta do seu papel."""

    rota = "experimento_global_autorizado_dpo_2026-10-17"
    modelo = "global.anthropic.claude-haiku-5-5"

    def __init__(self, modelo: _Modelo) -> None:
        self.m = modelo

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        enviado = "\n".join(str(c["text"]) for msg in mensagens for c in msg["content"])
        texto = await self.m(sistema, enviado, max_tokens)
        return RespostaDaRotaGlobal(texto=texto, parada="end_turn", entrada=1, saida=1, latencia_ms=1)


async def test_chave_desligada_e_a_helena_de_antes() -> None:
    grafo = _grafo(_Glm(), _HaikuPorPapel(_Modelo()))  # type: ignore[arg-type]
    estado = _estado("estou com dor de garganta desde ontem")
    assert await tj.conduzir(grafo, estado, _Decisao(), None, None) is None


async def test_fora_da_allowlist_e_a_helena_de_antes() -> None:
    grafo = _ligado(_grafo(_Glm(), _HaikuPorPapel(_Modelo())))
    estado = _estado("estou com dor de garganta desde ontem", conversa=_OUTRA)
    assert await tj.conduzir(grafo, estado, _Decisao(), None, None) is None


async def test_na_allowlist_pergunta_e_depois_oferece_a_equipe() -> None:
    grafo = _ligado(_grafo(_Glm(), _HaikuPorPapel(_Modelo(juiz="EQUIPE"))))
    estado = _estado("estou com dor de garganta desde ontem")
    primeiro = await tj.conduzir(grafo, estado, _Decisao(), None, None)
    assert primeiro is not None
    assert str(primeiro["coleta_pendente"]).startswith(tj.PENDENTE)
    assert json.loads(primeiro["coleta_contexto"])["pendente"] is not None
    turno = primeiro
    for _ in range(3):
        seguinte = _estado("nao tenho nada disso")
        seguinte["coleta_pendente"] = turno["coleta_pendente"]
        seguinte["coleta_contexto"] = turno["coleta_contexto"]
        proximo = await tj.conduzir(grafo, seguinte, _Decisao(), turno["coleta_pendente"], None)
        assert proximo is not None
        turno = proximo
        if not str(turno["coleta_pendente"] or "").startswith(tj.PENDENTE):
            break
    assert turno["coleta_pendente"] == PENDENTE_OFERTA_EQUIPE


async def test_contexto_perdido_no_meio_oferece_a_equipe() -> None:
    grafo = _ligado(_grafo(_Glm(), _HaikuPorPapel(_Modelo())))
    estado = _estado("nao")
    estado["coleta_pendente"] = f"{tj.PENDENTE}1"
    estado["coleta_contexto"] = ""
    r = await tj.conduzir(grafo, estado, _Decisao(), f"{tj.PENDENTE}1", None)
    assert r is not None
    assert r["coleta_pendente"] == PENDENTE_OFERTA_EQUIPE
