"""Simulador da HELENA V4 (DL-0115): D13 "caso evitavel" continua valendo para sintoma leve e duvida de saude.

* a checagem D13 vale com a v2 OU a v4 ligada (o parametro `triagem_v2` quer dizer "alguma das duas") e o
  `__main__` a liga pelas duas chaves do ambiente;
* na v4, a escolha da opcao 2 ("falar com a nossa equipe de saude") e' pedido de pessoa: o caso que ela abre
  nao e' evitavel; um caso sem alarme, sem risco e sem pedido de pessoa continua sendo;
* os textos fixos da v4 (pergunta de alerta, resposta final, especialista, carteirinha) passam nos passos do
  roteiro `helena_v4` que os esperam.
"""

from __future__ import annotations

import inspect

import pytest

from maezo.agents.helena import helena_v4 as v4
from maezo.agents.helena.graph import RESPOSTA_COBERTURA_ESPECIFICA
from maezo.ports.tina import ElegibilidadeView, VinculoPlano
from maezo.qa.simulador import __main__ as principal
from maezo.qa.simulador import aceite as ac
from maezo.qa.simulador import checagens as ch
from maezo.qa.simulador.personas import por_id

from .apoio_simulador import conversa, escalonamento, turno


def test_d13_continua_valendo_para_sintoma_leve_com_a_v4_ligada() -> None:
    c = conversa(
        turno(1, texto="Estou com dor nas costas", helena=["ok"]),
        turno(2, texto="Não", helena=["ok"], escalonamentos=[escalonamento("helena", "intencao_clinica")]),
        persona_id="sintoma_leve",
    )
    r = ch.d13_caso_evitavel(c, por_id("sintoma_leve"), triagem_v2=True)
    assert r.aplicavel and not r.passou
    assert "intencao_clinica" in r.achados[0].motivo
    # Com as duas chaves desligadas, a checagem nao se aplica.
    assert not ch.d13_caso_evitavel(c, por_id("sintoma_leve")).aplicavel


@pytest.mark.parametrize("escolha", ["2", "opção 2", "a 2", "falar com a equipe de saúde"])
def test_d13_a_escolha_da_opcao_2_e_pedido_de_pessoa(escolha: str) -> None:
    c = conversa(
        turno(1, texto="Estou com dor nas costas", helena=["ok"]),
        turno(2, texto="Não", helena=["ok"]),
        turno(3, texto=escolha, helena=["ok"], escalonamentos=[escalonamento("helena", "intencao_clinica")]),
        persona_id="sintoma_leve",
    )
    assert ch.d13_caso_evitavel(c, por_id("sintoma_leve"), triagem_v2=True).passou


def test_d13_nao_confunde_um_numero_qualquer_com_a_escolha() -> None:
    c = conversa(
        turno(1, texto="Estou com dor há 2 dias", helena=["ok"]),
        turno(
            2, texto="Tem 2 dias", helena=["ok"], escalonamentos=[escalonamento("helena", "intencao_clinica")]
        ),
        persona_id="sintoma_leve",
    )
    assert not ch.d13_caso_evitavel(c, por_id("sintoma_leve"), triagem_v2=True).passou


def test_o_main_liga_a_d13_pela_v2_ou_pela_v4() -> None:
    fonte = inspect.getsource(principal)
    assert 'getattr(composicao.estado.settings, "helena_triagem_v2", False) is True' in fonte
    assert 'getattr(composicao.estado.settings, "helena_v4", False) is True' in fonte


def test_os_textos_fixos_da_v4_passam_nos_passos_do_roteiro() -> None:
    """A resposta CERTA de cada passo (os textos que a Helena manda com a chave ligada) nao reprova."""
    seg = {s.nome: s for s in ac.HELENA_V4}
    eco = "dor nas costas desde ontem"
    segmentacao = "Ambulatorial + Hospitalar"

    def certo(nome: str, indice: int, texto: str, motivo: str | None = None) -> list[str]:
        passo = seg[nome].passos[indice]
        escalacoes = [{"agente": "helena", "motivo": motivo, "severidade": None}] if motivo else []
        return ac.avaliar_passo(passo, [("helena", texto)], escalacoes, None)

    cardiologista = v4.texto_do_especialista(
        "cardiologista", basica=False, segmentacao=segmentacao, frase_cobertura=None
    )
    assert certo("1-especialista-cardiologista", 0, cardiologista) == []
    dermatologista = v4.texto_do_especialista(
        "dermatologista", basica=False, segmentacao=None, frase_cobertura=None
    )
    assert certo("2-especialista-dermatologista", 0, dermatologista) == []
    endocrino = v4.texto_do_especialista(
        "endocrinologista",
        basica=False,
        segmentacao=segmentacao,
        frase_cobertura=RESPOSTA_COBERTURA_ESPECIFICA,
    )
    assert certo("3-especialista-pergunta-de-cobertura-da-consulta", 0, endocrino) == []
    assert certo("4-cobertura-de-exame-nao-e-especialista", 0, RESPOSTA_COBERTURA_ESPECIFICA) == []

    view = ElegibilidadeView(
        portable_subject_ref="amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b",
        ativo=True,
        vinculos=(
            VinculoPlano(
                carteirinha_mascarada="******4567",
                plano="PLANO SINTETICO ENFERMARIA",
                registro_ans_plano="999999",
                segmentacao="Ambulatorial + Hospitalar",
                acomodacao=None,
                tipo_contratacao=None,
                vigencia_inicio="2024-03-01",
                cancelamento=None,
                atendimento_liberado=True,
                situacao_vinculo="Ativo",
                situacao_contrato="Ativo",
                titular=True,
                relacao_dependencia=None,
                titular_ref=None,
            ),
        ),
        fonte_atualizada_em="2026-10-06T10:00:00Z",
        campos_ausentes=frozenset(),
    )
    assert certo("5-carteirinha", 0, v4.texto_da_carteirinha(v4.fatos_da_carteirinha(view))) == []

    pergunta = v4.texto_da_pergunta_de_alerta(v4.GRUPO_GERAL, eco)
    final = v4.texto_final_do_sintoma(eco)
    caminho = v4.texto_da_opcao_1(v4.POPULACAO_ADULTO)
    nome = "6-sintoma-leve-nao-e-opcao-1"
    assert certo(nome, 0, pergunta) == []
    assert certo(nome, 1, final) == []
    assert certo(nome, 2, caminho) == []

    filho = "seu filho está com tosse"
    nome = "7-filho-com-tosse-opcao-2"
    assert certo(nome, 0, v4.texto_da_pergunta_de_alerta(v4.GRUPO_CRIANCA, filho)) == []
    assert certo(nome, 1, v4.texto_final_do_sintoma(filho)) == []
    equipe = (
        "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe de saúde. "
        "Um profissional vai continuar o seu atendimento."
    )
    assert certo(nome, 2, equipe, "intencao_clinica") == []


def test_o_roteiro_reprova_o_texto_que_nao_usa_o_que_a_pessoa_disse() -> None:
    nome = "6-sintoma-leve-nao-e-opcao-1"
    passo = {s.nome: s for s in ac.HELENA_V4}[nome].passos[1]
    sem_eco = v4.texto_final_do_sintoma(v4.ECO_GENERICO)
    falhas = ac.avaliar_passo(passo, [("helena", sem_eco)], [], None)
    assert any("dor nas costas desde ontem" in f for f in falhas), falhas
