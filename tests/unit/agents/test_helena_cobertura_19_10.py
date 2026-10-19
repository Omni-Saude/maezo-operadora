"""Rodada do dono de 19/10/2026 (DL-0126): continuidade da cobertura, "para quem", a cobertura nunca pelo
modelo e o texto do cancelamento executado.

  * depois de uma resposta de cobertura, "Mamografia" e "e vasectomia?" continuam cobertura (consulta AMH);
  * "nao e' sobre mensalidade" nunca vai ao Lucas;
  * com dependentes, a Helena pergunta para quem antes de consultar; a resposta escolhe o beneficiario;
  * a redacao de saude reprova qualquer mencao a cobertura; o `inform` troca a afirmacao de cobertura.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena import cobertura_rol as cr
from maezo.agents.helena import graph as g
from maezo.agents.helena.graph import MARCADOR_RESPOSTA_DE_COBERTURA, HelenaGraph
from maezo.conhecimento import saude_gerativa as sg
from maezo.ports.tina import DependenteAnonimo, DependentesView
from tests.unit.agents.test_helena_fases_18_10 import _Cob, _Item
from tests.unit.agents.test_helena_saude_gerativa import _DONO, _OUTRA, _Conversa

_REF = "amh:psr:v1:x"


# =================================================================================================
# Continuidade e cobranca negada (funcoes puras)
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "termo"),
    [
        ("Mamografia", "mamografia"),
        ("e vasectomia?", "vasectomia"),
        ("E a vasectomia?", "vasectomia"),
        ("O nome certo é mamografia bilateral", "mamografia bilateral"),
    ],
)
def test_o_nome_do_procedimento_e_a_continuacao(mensagem: str, termo: str) -> None:
    assert cr.termo_da_continuacao(mensagem) == termo


def test_mensagem_longa_nao_e_continuacao() -> None:
    longa = (
        "eu queria saber uma coisa sobre o meu filho que esta com febre desde ontem e nao melhora com nada"
    )
    assert cr.termo_da_continuacao(longa) is None


@pytest.mark.parametrize(
    "mensagem",
    [
        "Não, a minha dúvida não é sobre mensalidade",
        "não é sobre o boleto, é se o plano cobre mamografia",
        "nao tem a ver com pagamento",
    ],
)
def test_cobranca_negada_nao_e_pergunta_de_cobranca(mensagem: str) -> None:
    assert g._pergunta_de_cobranca(mensagem) is False


def test_a_cobranca_de_verdade_continua_cobranca() -> None:
    assert g._pergunta_de_cobranca("quero a segunda via do boleto")


# =================================================================================================
# Para quem
# =================================================================================================


def test_pergunta_para_quem_lista_cada_dependente() -> None:
    texto = cr.pergunta_para_quem(["filho ou filha", "cônjuge"])
    assert "1) para você (titular)" in texto
    assert "2) para o dependente 1 (filho ou filha)" in texto
    assert "3) para o dependente 2 (cônjuge)" in texto


@pytest.mark.parametrize(
    ("resposta", "quem"),
    [("1", "titular"), ("para mim", "titular"), ("2", "dependente:1"), ("3", "dependente:2"), ("ok", None)],
)
def test_quem_da_resposta(resposta: str, quem: str | None) -> None:
    assert cr.quem_da_resposta(resposta, 2) == quem


def test_numero_fora_da_lista_nao_e_escolha() -> None:
    assert cr.quem_da_resposta("5", 2) is None


# =================================================================================================
# Grafo: continuidade, para quem e dependente
# =================================================================================================


class _Fonte:
    def __init__(self, view: Any, dependentes: Any = None) -> None:
        self.view = view
        self.dependentes = dependentes
        self.termos: list[str] = []

    async def consultar(self, ref: str, subtipo: str) -> Any:
        return self.dependentes if subtipo == "dependentes" else None

    async def consultar_cobertura(self, ref: str, procedimento: str) -> Any:
        self.termos.append(procedimento)
        return self.view


def _dependentes(*relacoes: str) -> DependentesView:
    return DependentesView(
        portable_subject_ref=_REF,
        eh_titular=True,
        total_ativos=len(relacoes),
        lista_truncada=False,
        dependentes=tuple(
            DependenteAnonimo(relacao_dependencia=r, situacao_vinculo="Apto", rn_no_plano=False)
            for r in relacoes
        ),
        fonte_atualizada_em="2026-10-13",
    )


def _grafo(fonte: _Fonte) -> HelenaGraph:
    conversa = _Conversa()
    return HelenaGraph(
        inference=conversa.modelo,  # type: ignore[arg-type]
        dmn=None,  # type: ignore[arg-type]
        cibseven=conversa.cib,
        audit_sink=None,  # type: ignore[arg-type]
        whatsapp=conversa.whatsapp,
        consultas_plano=fonte,  # type: ignore[arg-type]
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=conversa.base,
        saude_gerativa=conversa.redator,
    )


_MAMOGRAFIA = _Cob((_Item("Mamografia convencional", "coberto_rol"),))


def _depois_da_cobertura(mensagem: str, conversa: str = _DONO) -> Any:
    return {
        "conversation_id": conversa,
        "message_body": mensagem,
        "historico_conversa": [
            {"papel": "pessoa", "texto": "Meu plano cobre exame de mama?"},
            {"papel": "helena", "texto": MARCADOR_RESPOSTA_DE_COBERTURA},
        ],
    }


@pytest.mark.parametrize("mensagem", ["Mamografia", "E vasectomia?"])
def test_depois_da_cobertura_o_procedimento_continua_cobertura(mensagem: str) -> None:
    grafo = _grafo(_Fonte(_MAMOGRAFIA))
    estado = _depois_da_cobertura(mensagem)
    decisao = grafo._continuidade_da_cobertura(estado, g._plano(mensagem), mensagem)
    assert decisao is not None and decisao["intent"] == g.INTENT_CONSULTA_PLANO


def test_sem_resposta_de_cobertura_antes_o_procedimento_segue_o_caminho_de_sempre() -> None:
    grafo = _grafo(_Fonte(_MAMOGRAFIA))
    estado: Any = {"conversation_id": _DONO, "message_body": "Mamografia", "historico_conversa": []}
    assert grafo._continuidade_da_cobertura(estado, "mamografia", "Mamografia") is None


def test_cobranca_negada_com_cobertura_vai_a_consulta() -> None:
    grafo = _grafo(_Fonte(_MAMOGRAFIA))
    mensagem = "Não, a minha dúvida não é sobre mensalidade, é se o plano cobre mamografia"
    estado: Any = {"conversation_id": _DONO, "message_body": mensagem, "historico_conversa": []}
    decisao = grafo._continuidade_da_cobertura(estado, g._plano(mensagem), mensagem)
    assert decisao is not None and decisao["intent"] == g.INTENT_CONSULTA_PLANO


async def test_a_continuacao_consulta_o_procedimento_pelo_texto() -> None:
    fonte = _Fonte(_MAMOGRAFIA)
    resposta = await _grafo(fonte)._cobertura_do_procedimento(_depois_da_cobertura("Mamografia"), _REF)
    assert resposta is not None and "Mamografia convencional" in resposta["response_text"]
    assert fonte.termos == ["mamografia"]


async def test_com_dependentes_pergunta_para_quem_e_depois_consulta_o_escolhido() -> None:
    fonte = _Fonte(_MAMOGRAFIA, _dependentes("Filho"))
    grafo = _grafo(fonte)
    estado: Any = {"conversation_id": _DONO, "message_body": "Meu plano cobre mamografia?"}
    pergunta = await grafo._cobertura_do_procedimento(estado, _REF)
    assert pergunta is not None
    assert pergunta["coleta_pendente"] == cr.PENDENTE_COBERTURA_PARA_QUEM
    assert pergunta["coleta_contexto"] == "mamografia|1"
    assert "1) para você (titular)" in pergunta["response_text"]
    assert "2) para o dependente 1" in pergunta["response_text"]
    assert fonte.termos == [], "nao consulta antes de saber para quem"

    escolha: Any = {
        "conversation_id": _DONO,
        "message_body": "2",
        "coleta_pendente": cr.PENDENTE_COBERTURA_PARA_QUEM,
        "coleta_contexto": "mamografia|1",
    }
    rota = grafo._continuidade_da_cobertura(escolha, "2", "2")
    assert rota is not None and rota["coleta_pendente"] == f"{cr.PREFIXO_COBERTURA_QUEM}dependente:1"
    consulta: Any = {
        "conversation_id": _DONO,
        "message_body": "2",
        "coleta_pendente": rota["coleta_pendente"],
        "coleta_contexto": rota["coleta_contexto"],
    }
    resposta = await grafo._cobertura_do_procedimento(consulta, _REF)
    assert resposta is not None and resposta["response_text"].endswith(cr.AVISO_DO_DEPENDENTE)
    assert fonte.termos == ["mamografia"]
    assert resposta["coleta_pendente"] is None


async def test_para_o_titular_nao_ha_aviso_do_dependente() -> None:
    fonte = _Fonte(_MAMOGRAFIA, _dependentes("Filho"))
    consulta: Any = {
        "conversation_id": _DONO,
        "message_body": "1",
        "coleta_pendente": f"{cr.PREFIXO_COBERTURA_QUEM}{cr.QUEM_TITULAR}",
        "coleta_contexto": "mamografia",
    }
    resposta = await _grafo(fonte)._cobertura_do_procedimento(consulta, _REF)
    assert resposta is not None and cr.AVISO_DO_DEPENDENTE not in resposta["response_text"]


async def test_sem_dependentes_consulta_direto() -> None:
    fonte = _Fonte(_MAMOGRAFIA)
    estado: Any = {"conversation_id": _DONO, "message_body": "Meu plano cobre mamografia?"}
    resposta = await _grafo(fonte)._cobertura_do_procedimento(estado, _REF)
    assert resposta is not None and fonte.termos == ["mamografia"]


async def test_fora_da_allowlist_nada_muda() -> None:
    grafo = _grafo(_Fonte(_MAMOGRAFIA, _dependentes("Filho")))
    estado: Any = {"conversation_id": _OUTRA, "message_body": "Meu plano cobre mamografia?"}
    assert await grafo._cobertura_do_procedimento(estado, _REF) is None


# =================================================================================================
# Cobertura nunca pelo modelo
# =================================================================================================


@pytest.mark.parametrize(
    "texto",
    [
        "A mamografia é um exame das mamas. O plano cobre esse exame.",
        "A vasectomia tem cobertura e consta no Rol.",
        "O exame costuma ser coberto.",
    ],
)
def test_a_redacao_de_saude_reprova_falar_de_cobertura(texto: str) -> None:
    pedido = sg.Pedido(modo="informar", mensagem="o que e mamografia?")
    regras = [v.regra for v in sg.verificar(texto, pedido, cercas=lambda _t: ())]
    assert "fala_de_cobertura" in regras


def test_o_prompt_proibe_falar_de_cobertura() -> None:
    assert "Nunca fala de cobertura do plano" in sg.PROMPT_SISTEMA


@pytest.mark.parametrize(
    "texto",
    [
        "Sim, o seu plano tem cobertura para mamografia.",
        "A vasectomia é coberta pelo plano.",
        "O plano paga.",
    ],
)
def test_o_inform_reconhece_afirmacao_de_cobertura(texto: str) -> None:
    assert g._AFIRMA_COBERTURA.search(g._plano(texto))


def test_o_inform_nao_barra_texto_sem_afirmacao() -> None:
    assert not g._AFIRMA_COBERTURA.search(g._plano("A mamografia é um exame de imagem das mamas."))


def test_outro_procedimento_depois_do_para_quem_continua_cobertura() -> None:
    grafo = _grafo(_Fonte(_MAMOGRAFIA, _dependentes("Filho")))
    estado: Any = {
        "conversation_id": _DONO,
        "message_body": "Mamografia",
        "coleta_pendente": cr.PENDENTE_COBERTURA_PARA_QUEM,
        "coleta_contexto": "exame de mama|1",
        "historico_conversa": [
            {"papel": "pessoa", "texto": "Meu plano cobre exame de mama?"},
            {"papel": "helena", "texto": cr.pergunta_para_quem(["filho ou filha"])},
        ],
    }
    decisao = grafo._continuidade_da_cobertura(estado, "mamografia", "Mamografia")
    assert decisao is not None and decisao["intent"] == g.INTENT_CONSULTA_PLANO
    assert not decisao.get("coleta_pendente"), "a pergunta 'para quem' sera' refeita com o termo novo"


# =================================================================================================
# Roteiros de 19/10/2026 (imagem 0089bb13): PSA e "E Osteoporose?"
# =================================================================================================


def test_a_redacao_de_saude_reprova_o_medico_vai_avaliar() -> None:
    texto = "O PSA mede uma proteína da próstata. O médico vai avaliar seu resultado junto com outros exames."
    assert "promessa_sem_start" in HelenaGraph._cercas_da_helena(texto)
    assert HelenaGraph._cercas_da_helena("Vale levar o resultado ao seu médico.") == []
    pedido = sg.Pedido(modo="resultado", mensagem="meu psa deu alto")
    regras = [v.regra for v in sg.verificar(texto, pedido, cercas=HelenaGraph._cercas_da_helena)]
    assert "cerca:promessa_sem_start" in regras
    assert "medico" in sg.instrucao_de_reescrita([sg.Violacao("cerca:promessa_sem_start")])


@pytest.mark.parametrize("forma", sg.FORMAS_DA_OFERTA)
def test_as_ofertas_fixas_passam_na_cerca_da_redacao(forma: str) -> None:
    assert HelenaGraph._cercas_da_helena(forma) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Não use suplemento de cálcio por conta própria.",
        "Não tome nada antes de falar com o médico.",
        "Não use remédio sem acompanhamento médico.",
    ],
)
def test_nao_automedicar_nao_e_proibicao_de_remedio(texto: str) -> None:
    assert not sg._NAO_DE.search(g._plano(texto))


def test_proibir_remedio_continua_reprovado() -> None:
    assert sg._NAO_DE.search(g._plano("Não use esse remédio."))
