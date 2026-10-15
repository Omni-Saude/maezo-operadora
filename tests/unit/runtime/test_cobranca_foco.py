"""DL-0087 (10/10/2026): o FOCO da pergunta de valor sai do texto por um lexico pequeno e deterministico."""

from __future__ import annotations

import pytest

from maezo.runtime.cobranca_foco import COBRANCA_FOCOS, decidir_foco_da_cobranca, foco_valido


def test_o_dominio_e_fechado_e_e_o_do_dono() -> None:
    assert (
        frozenset(
            {
                "valor",
                "coparticipacao",
                "saldo",
                "pagamento",
                "vencimento",
                "geral",
                # Rodada 3 do simulador (13/10/2026): confirmacao da mais recente e esclarecimento.
                "ultima",
                "esclarecimento",
                # Rodada 5: extrato detalhado e pagamento direto ao prestador.
                "extrato",
                "pagamento_ao_prestador",
                # Rodada 6: como pagar (depois do vencimento) e "ja' paguei dia 06".
                "como_pagar",
                "ja_paguei",
                # Red team de 15/10/2026 (L-02 e L-04): consequencia de nao pagar e link de pagamento suspeito.
                "consequencia_do_atraso",
                "link_suspeito",
            }
        )
        == COBRANCA_FOCOS
    )


@pytest.mark.parametrize(
    ("texto", "foco"),
    [
        # coparticipacao
        ("Quanto paguei de coparticipação este mês?", "coparticipacao"),
        ("qual a COPART de outubro", "coparticipacao"),
        # saldo
        ("quanto devo?", "saldo"),
        ("tenho algum saldo?", "saldo"),
        ("o que esta em aberto", "saldo"),
        ("quanto falta pagar", "saldo"),
        # "quanto paguei" e' VALOR, antes de "paguei" (pagamento)
        ("quanto paguei em maio?", "valor"),
        # pagamento
        ("quando paguei a mensalidade de setembro?", "pagamento"),
        ("ja paguei julho?", "pagamento"),
        ("o pagamento de agosto entrou?", "pagamento"),
        # vencimento
        ("quando vence a mensalidade?", "vencimento"),
        ("qual o vencimento de novembro", "vencimento"),
        # valor
        ("qual foi o valor da mensalidade de julho?", "valor"),
        ("quanto foi a de agosto", "valor"),
        ("quanto é a mensalidade?", "valor"),
        # geral
        ("me fala da mensalidade de julho", "geral"),
        ("", "geral"),
    ],
)
def test_lexico_e_precedencia(texto: str, foco: str) -> None:
    assert decidir_foco_da_cobranca(texto) == foco


@pytest.mark.parametrize("bruto", [None, 1, ["valor"], {"texto": "valor"}])
def test_entrada_que_nao_e_texto_e_geral(bruto: object) -> None:
    assert decidir_foco_da_cobranca(bruto) == "geral"


def test_palavra_parecida_nao_casa() -> None:
    # "devolucao" nao e' "devo"; "valorizar" nao e' "valor"; "pagamentos" nao e' "pagamento".
    assert decidir_foco_da_cobranca("quero a devolucao") == "geral"
    assert decidir_foco_da_cobranca("valorizo o atendimento") == "geral"


@pytest.mark.parametrize("valor", [*sorted(COBRANCA_FOCOS)])
def test_foco_valido_aceita_o_dominio(valor: str) -> None:
    assert foco_valido(valor)


@pytest.mark.parametrize("valor", ["boleto", "", None, 1, "VALOR"])
def test_foco_valido_recusa_o_resto(valor: object) -> None:
    assert not foco_valido(valor)
