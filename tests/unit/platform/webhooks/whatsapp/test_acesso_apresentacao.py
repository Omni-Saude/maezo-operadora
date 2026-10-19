"""Mensagem de apresentacao do canal (`apresentacao-canal-v1`, 14/10/2026): texto exato e os dois momentos."""

from __future__ import annotations

from datetime import timedelta

import pytest

from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import apresentacao_canal as ap
from maezo.platform.webhooks.whatsapp import pre_roteamento
from tests.unit.platform.webhooks.whatsapp.test_acesso import (
    CONV,
    CPF,
    REF,
    TENANT_AMH,
    Ambiente,
)


@pytest.fixture
def amb() -> Ambiente:
    return Ambiente()


PRIMEIRA = (ap.TEXTO_VERIFICADO_APRESENTACAO, ap.TEXTO_APRESENTACAO_CANAL_SEM_DEPENDENTES)


def test_texto_exato_com_dependentes() -> None:
    assert ap.VERSAO_APRESENTACAO_CANAL == "apresentacao-canal-v2"
    assert ap.texto_apresentacao(com_dependentes=True) == (
        "Por aqui você pode:\n"
        "• Consultar mensalidade, vencimento e coparticipação.\n"
        "• Ver os dados do seu plano: carteirinha, carências e dependentes.\n"
        "• Tirar dúvidas de saúde e saber onde buscar atendimento.\n"
        "• Falar com a nossa equipe sobre saúde ou cobrança, quando precisar.\n"
        "Em emergência, ligue 192. Como posso ajudar?"
    )


def test_texto_exato_sem_dependentes() -> None:
    texto = ap.texto_apresentacao(com_dependentes=False)
    assert "dependentes" not in texto
    assert "• Ver os dados do seu plano: carteirinha e carências.\n" in texto
    assert texto == ap.TEXTO_APRESENTACAO_CANAL.replace(
        "carteirinha, carências e dependentes", "carteirinha e carências"
    )
    assert len(texto.splitlines()) <= 6 and "😊" not in texto
    assert ap.TEXTO_VERIFICADO_APRESENTACAO == "Pronto, já confirmei que é você 😊"


def _servico(amb: Ambiente, **kw: object) -> ac.AcessoBeneficiario:
    return ac.AcessoBeneficiario(
        store=amb.store, verification=amb.verif, consents=amb.consent, resolvedor=amb.resolvedor,
        hash_telefone=lambda n: "a" * 64, hasher=amb.hasher, lexicos=pre_roteamento.carregar(),
        amh_tenant=TENANT_AMH, hash_scheme="amh-subject-verify-v1", purpose_of_use="x",
        relogio=amb.relogio, **kw,  # type: ignore[arg-type]
    )  # fmt: skip


async def test_com_a_fonte_de_dependentes_o_item_aparece() -> None:
    amb = Ambiente()
    amb.servico = _servico(amb, apresentar_dependentes=True)
    d = await amb.verificar_pelo_telefone()
    assert d.respostas == (ap.TEXTO_VERIFICADO_APRESENTACAO, ap.TEXTO_APRESENTACAO_CANAL)


async def test_primeira_verificacao_mostra_uma_vez(amb: Ambiente) -> None:
    d = await amb.verificar_pelo_telefone()
    assert d.respostas == PRIMEIRA and d.novo.apresentado_em == amb.relogio.agora
    assert (await amb.msg("oi")).respostas == ()  # a conversa segue: nada repetido


async def test_reverificacao_do_dia_seguinte_nao_repete(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(hours=25)
    d = await amb.msg("oi")
    assert d.respostas == (ac.PEDIDO_CPF,)
    d = await amb.msg(CPF)
    assert d.respostas == (ac.RESPOSTA_VERIFICADO,)


async def test_volta_apos_31_dias_mostra_na_proxima_verificacao_uma_vez(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(days=31)
    d = await amb.msg("oi")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.apresentado_em is None
    d = await amb.msg(CPF)
    assert d.respostas == PRIMEIRA  # uma vez so', ao fim da reverificacao (nunca antes e depois)
    amb.relogio.avancar(hours=25)
    await amb.msg("oi")
    assert (await amb.msg(CPF)).respostas == (ac.RESPOSTA_VERIFICADO,)


async def test_29_dias_nao_mostra(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(days=29)
    d = await amb.msg("oi")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.apresentado_em is not None
    assert (await amb.msg(CPF)).respostas == (ac.RESPOSTA_VERIFICADO,)


async def test_entrega_duplicada_nao_duplica(amb: Ambiente) -> None:
    amb.verif.cadastrar_cpf(CPF, REF)
    await amb.ate_cpf()
    # Reentrega ANTES de gravar (o envio caiu): recalcula igual, nada gravado.
    d1 = await amb.servico.avaliar(conversation_id=CONV, texto=CPF, numero_cru="5511999990000")
    d2 = await amb.servico.avaliar(conversation_id=CONV, texto=CPF, numero_cru="5511999990000")
    assert d1.respostas == d2.respostas == PRIMEIRA
    await amb.servico.persistir(d1)
    # Reentrega DEPOIS de gravar: a conversa ja' esta' verificada, nada de apresentacao.
    d3 = await amb.servico.avaliar(conversation_id=CONV, texto=CPF, numero_cru="5511999990000")
    assert d3.respostas == () and d3.prosseguir


async def test_outra_conversa_nao_e_afetada(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    outra = "wa:austa:hk1_" + "cd" * 8
    amb.relogio.avancar(days=40)
    d = await amb.servico.avaliar(conversation_id=outra, texto="oi", numero_cru="5511999990001")
    assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and d.novo.apresentado_em is None
    antiga = await amb.store.ler(CONV)
    assert antiga is not None and antiga.apresentado_em is not None  # nao foi tocada


def test_silencio_tem_de_ser_maior_que_a_validade(amb: Ambiente) -> None:
    with pytest.raises(ValueError, match="silencio"):
        _servico(amb, silencio_apresentacao=timedelta(hours=24))
