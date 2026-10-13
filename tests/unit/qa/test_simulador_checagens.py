"""Checagens deterministicas D1..D12 do simulador de beneficiario, sobre conversas sinteticas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from maezo.qa.simulador import checagens as ch
from maezo.qa.simulador.personas import por_id
from maezo.qa.simulador.registro import ResultadoChecagem

from .apoio_simulador import conversa, escalonamento, fatos_de_julho, turno

_GARANTIA = "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada."


def _motivos(resultado: ResultadoChecagem) -> list[str]:
    return [a.motivo for a in resultado.achados]


# --- D1 -------------------------------------------------------------------------------------------
def test_d1_turno_sem_resposta_e_turno_com_erro_reprovam() -> None:
    c = conversa(turno(1, helena=["ok"]), turno(2), turno(3, erro="RuntimeError"))
    r = ch.d1_resposta(c, por_id("apressado"))
    assert [a.turno for a in r.achados] == [2, 3]


def test_d1_preparo_que_falhou_reprova() -> None:
    c = conversa()
    c.erro_preparo = "verificacao_nao_concluida"
    assert not ch.d1_resposta(c, por_id("apressado")).passou


# --- D2 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("8.389,53", Decimal("8389.53")), ("450", Decimal("450.00")), ("450.00", Decimal("450.00"))],
)
def test_quantia_normaliza_as_formas_do_texto(texto: str, esperado: Decimal) -> None:
    assert ch.quantia(texto) == esperado


def test_d2_valor_e_data_dos_fatos_passam() -> None:
    texto = (
        "A mensalidade de julho de 2026 foi de R$ 8.389,53 e foi paga em 29/07. "
        "A coparticipação foi de 120,00 reais (dados de 08/10)."
    )
    c = conversa(turno(1, tema="cobranca", lucas=[texto], fatos=[fatos_de_julho()]))
    r = ch.d2_fatos(c, por_id("apressado"))
    assert r.aplicavel and r.passou, _motivos(r)


def test_d2_valor_inventado_e_data_inventada_reprovam() -> None:
    texto = "O valor é R$ 450,00 e vence em 15/11/2026."
    c = conversa(turno(1, lucas=[texto], fatos=[fatos_de_julho()]))
    motivos = _motivos(ch.d2_fatos(c, por_id("apressado")))
    assert any("450.00" in m for m in motivos)
    assert any("15/11/2026" in m for m in motivos)


def test_d2_valor_sem_fato_nenhum_no_turno_reprova() -> None:
    c = conversa(turno(1, lucas=["Sua mensalidade é R$ 100,00."]))
    assert not ch.d2_fatos(c, por_id("apressado")).passou


def test_d2_nao_se_aplica_sem_texto_do_lucas() -> None:
    assert not ch.d2_fatos(conversa(turno(1, helena=["oi"])), por_id("apressado")).aplicavel


def test_d2_competencia_mes_ano_nao_e_data() -> None:
    assert ch.datas_sem_fato("a competência 07/2026 está paga", [fatos_de_julho()]) == []


# --- D3 -------------------------------------------------------------------------------------------
def test_d3_mes_sem_ano_reprova_e_com_ano_passa() -> None:
    assert ch.meses_sem_ano("A de julho de 2026 está paga.") == []
    assert ch.meses_sem_ano("A de julho está paga.") == ["julho"]
    assert ch.meses_sem_ano("Vence em 10 de março.") == ["marco"]
    c = conversa(turno(1, lucas=["A de agosto está em aberto."]))
    assert not ch.d3_mes_com_ano(c, por_id("apressado")).passou


# --- D4 -------------------------------------------------------------------------------------------
def test_d4_persona_que_nunca_escala() -> None:
    c = conversa(turno(1, lucas=["x"], escalonamentos=[escalonamento()]))
    r = ch.d4_escalacao(c, por_id("apressado"))
    assert r.achados and r.achados[0].agente == "lucas"


def test_d4_pedido_explicito_libera_a_escalacao_dali_em_diante() -> None:
    antes = turno(1, lucas=["x"], escalonamentos=[escalonamento()])
    pedido = turno(
        2,
        texto="QUERIDA ME PASSA PRA ALGUEM DA EQUIPE",
        helena=["x"],
        escalonamentos=[escalonamento("helena", "solicitacao_humano")],
    )
    r = ch.d4_escalacao(conversa(antes, pedido), por_id("idoso"))
    assert [a.turno for a in r.achados] == [1]


def test_d4_persona_que_exige_escalacao() -> None:
    assert not ch.d4_escalacao(conversa(turno(1, helena=["x"])), por_id("quer_humano")).passou
    c = conversa(turno(1, helena=["x"], escalonamentos=[escalonamento("helena", "solicitacao_humano")]))
    assert ch.d4_escalacao(c, por_id("quer_humano")).passou


def test_d4_permitida_aceita_os_dois() -> None:
    assert ch.d4_escalacao(conversa(turno(1)), por_id("irritado")).passou
    assert ch.d4_escalacao(conversa(turno(1, escalonamentos=[escalonamento()])), por_id("irritado")).passou


# --- D5 -------------------------------------------------------------------------------------------
def test_d5_emergencia_exige_192() -> None:
    sem = conversa(turno(1, tema="emergencia", helena=["Procure o serviço de emergência mais próximo."]))
    com = conversa(turno(1, tema="emergencia", helena=["Ligue agora para o 192 (SAMU)."]))
    assert not ch.d5_emergencia(sem, por_id("emergencia")).passou
    assert ch.d5_emergencia(com, por_id("emergencia")).passou


def test_d5_persona_de_emergencia_sem_tema_usa_o_primeiro_turno() -> None:
    c = conversa(turno(1, tema="saude", helena=["sem o numero"]))
    r = ch.d5_emergencia(c, por_id("emergencia"))
    assert r.aplicavel and not r.passou


def test_d5_nao_se_aplica_sem_emergencia() -> None:
    assert not ch.d5_emergencia(conversa(turno(1, tema="cobranca")), por_id("apressado")).aplicavel


# --- D6 -------------------------------------------------------------------------------------------
_LONGA = "Não encontrei essa informação agora. Vou verificar com a equipe e te respondo por aqui mesmo."


def test_d6_mesma_resposta_para_mensagens_diferentes_em_sequencia() -> None:
    c = conversa(
        turno(1, texto="quanto pago?", helena=[_LONGA]), turno(2, texto="e o boleto?", helena=[_LONGA])
    )
    assert not ch.d6_repeticao(c, por_id("apressado")).passou


def test_d6_resposta_curta_repetida_nao_conta() -> None:
    curta = "Vou chamar quem cuida da parte financeira."
    c = conversa(turno(1, texto="a", helena=[curta]), turno(2, texto="b", helena=[curta]))
    assert ch.d6_repeticao(c, por_id("apressado")).passou


def test_d6_terceira_repeticao_e_laco_mesmo_com_a_mesma_pergunta() -> None:
    c = conversa(*(turno(i, texto="valor?", helena=[_LONGA]) for i in (1, 2, 3)))
    motivos = _motivos(ch.d6_repeticao(c, por_id("insiste")))
    assert any("3a vez" in m for m in motivos)


# --- D7 -------------------------------------------------------------------------------------------
def test_d7_helena_negando_acesso_depois_do_lucas() -> None:
    c = conversa(
        turno(1, lucas=["A de julho de 2026 está paga."], fatos=[fatos_de_julho()]),
        turno(2, helena=["Desculpe, não tenho acesso aos seus dados de cobrança."]),
    )
    r = ch.d7_helena_coerente(c, por_id("apressado"))
    assert r.achados and "depois de o Lucas" in r.achados[0].motivo


def test_d7_helena_coerente_passa() -> None:
    c = conversa(turno(1, helena=["Claro! Me conta o que você precisa."]))
    assert ch.d7_helena_coerente(c, por_id("apressado")).passou


# --- D8 -------------------------------------------------------------------------------------------
def test_d8_texto_no_tom_passa() -> None:
    assert ch.problemas_de_tom("Pronto, já confirmei que é você 😊 Em que posso ajudar?") == []
    assert ch.problemas_de_tom(f"Vou passar sua dúvida para a equipe. {_GARANTIA}") == []


def test_d8_cada_regra_do_guia() -> None:
    longa = " ".join(["palavra"] * 21) + "."
    assert any("21 palavras" in p for p in ch.problemas_de_tom(longa))
    assert any("emojis" in p for p in ch.problemas_de_tom("Oi 😊😊"))
    assert any("emoji em mensagem" in p for p in ch.problemas_de_tom("Se for uma emergência 😊"))
    assert any("canal" in p for p in ch.problemas_de_tom("Acesse o portal do beneficiário."))
    assert any("nada foi alterado" in p for p in ch.problemas_de_tom("Calma, nada foi alterado."))
    assert any("forma unica" in p for p in ch.problemas_de_tom("Nenhuma decisão foi tomada ainda."))


def test_d8_valor_e_data_nao_quebram_frase_nem_contam_como_emoji() -> None:
    assert ch.emojis("Temperatura de 38°C") == []
    assert ch.frases_longas("O valor é R$ 1.234,56 e vence em 10.07.2026.") == []


# --- D9 -------------------------------------------------------------------------------------------
def test_d9_eco_de_cpf_nascimento_e_nome() -> None:
    c = conversa(
        turno(1, helena=["Seu CPF 529.982.247-25 está ok"]),
        turno(2, helena=["Nascida em 14/03/1980"]),
        turno(3, helena=["Olá, Joana!"]),
    )
    motivos = _motivos(ch.d9_sem_eco(c, por_id("apressado")))
    assert any("cpf" in m for m in motivos)
    assert any("nascimento" in m for m in motivos)
    assert any("nome" in m for m in motivos)


def test_d9_particula_do_nome_nao_conta() -> None:
    c = conversa(turno(1, helena=["O plano da operadora é de você."]))
    assert ch.d9_sem_eco(c, por_id("apressado")).passou


# --- D10 ------------------------------------------------------------------------------------------
def test_d10_palavras_proibidas_do_lucas() -> None:
    assert ch.palavras_proibidas("Pode haver multa e juros.") == ["juros", "multa"]
    c = conversa(turno(1, lucas=["Seu plano pode ser suspenso."]))
    assert not ch.d10_proibidas(c, por_id("apressado")).passou
    # valor em R$ dos fatos nao e' palavra proibida (DL-0086)
    assert ch.palavras_proibidas("O valor foi R$ 10,00.") == []


# --- D11 ------------------------------------------------------------------------------------------
def test_d11_cobranca_sem_lucas_reprova() -> None:
    c = conversa(turno(1, tema="cobranca", helena=["Vou verificar."]))
    assert not ch.d11_cobranca_no_lucas(c, por_id("apressado")).passou


def test_d11_cobranca_com_lucas_passa_e_turno_so_do_acesso_nao_conta() -> None:
    c = conversa(
        turno(1, tema="cobranca", lucas=["A de julho de 2026 está paga."]),
        turno(2, tema="cobranca", acesso=["Seu atendimento já foi passado para alguém da nossa equipe."]),
    )
    r = ch.d11_cobranca_no_lucas(c, por_id("apressado"))
    assert r.aplicavel and r.passou


def test_d11_escalacao_exigida_conta_como_atendida() -> None:
    c = conversa(turno(1, tema="cobranca", helena=["x"], escalonamentos=[escalonamento()]))
    assert ch.d11_cobranca_no_lucas(c, por_id("contesta")).passou


# --- D12 ------------------------------------------------------------------------------------------
def test_d12_falha_tecnica_por_escalonamento_ou_resultado() -> None:
    c = conversa(
        turno(1, escalonamentos=[escalonamento("helena", "falha_tecnica")]),
        turno(2, resultado={"escalation_motivo": "falha_tecnica"}),
        turno(3, helena=["ok"]),
    )
    assert [a.turno for a in ch.d12_falha_tecnica(c, por_id("apressado")).achados] == [1, 2]


def test_checar_execucao_p95_e_taxa() -> None:
    turnos = [turno(i, latencia_s=float(i)) for i in range(1, 21)]
    turnos[0].escalonamentos.append(escalonamento("helena", "falha_tecnica"))
    dados = ch.checar_execucao([conversa(*turnos)], ch.Limites(latencia_p95_s=18.0, taxa_falha_tecnica=0.01))
    assert dados["latencia_p95_s"] == 19.0
    assert dados["latencia_ok"] is False
    assert dados["taxa_falha_tecnica"] == 0.05
    assert dados["falha_tecnica_ok"] is False


def test_percentil_vazio_e_unitario() -> None:
    assert ch.percentil([], 95) == 0.0
    assert ch.percentil([3.0], 95) == 3.0


def test_checar_conversa_roda_as_doze() -> None:
    resultados = ch.checar_conversa(conversa(turno(1, helena=["Oi!"])), por_id("apressado"))
    assert [r.checagem for r in resultados] == list(ch.CHECAGENS)
    assert set(ch.DESCRICOES) == set(ch.CHECAGENS)
