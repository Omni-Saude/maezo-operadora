"""Os tres estados de um fato no dossie — e por que confundir dois deles apaga o caso.

DEFEITO QUE MOTIVOU ESTE ARQUIVO (medido em 19/08/2026, corrigido em 24/08)

Num caso com prestador FORA da rede, o dossie escreveu:

    "Nao ha registro de verificacao de teto L2 ou rede credenciada"

A rede tinha sido verificada e dado FALSO. O teto e' que nao tinha valor. As duas coisas
viraram a mesma frase, e a frase escolhida foi a errada: para um medico auditor, "nao ha
registro" pede conferencia e "nao esta na rede" e' motivo de negativa. O dossie apagou a
unica informacao desfavoravel do caso — que era, justamente, a que importava.

A CAUSA NAO ERA A QUE PARECIA. O plano de teste atribuiu o defeito a montagem ("o codigo
descarta valores vazios e leva o falso junto"). Nao descarta: `_build_dossier` sempre passou
`False` e `None` adiante, os dois. A distincao morria na PASSAGEM PARA O MODELO — os fatos
iam como repr de dicionario Python, onde `False` e `None` parecem igualmente "vazios", e
nada dizia ao modelo que um deles e' fato apurado.

Estes testes travam a renderizacao. Nao testam o modelo: testam que o modelo recebe tres
formas distinguiveis, que e' a parte que estava faltando e a unica deterministica.
"""

from __future__ import annotations

import pytest

from maezo.agents.rafael.graph import _FATOS_BOOLEANOS, render_fatos_para_prompt
from maezo.agents.rafael.prompts import dossier_prompt


def test_verificado_negativo_e_nao_verificado_nao_se_parecem() -> None:
    """O caso exato que falhou: rede verificada-e-falsa ao lado de teto sem valor."""
    saida = render_fatos_para_prompt({"rede_credenciada": False, "dentro_teto_l2": None})

    linha_rede = next(l for l in saida.splitlines() if "rede credenciada" in l)
    linha_teto = next(l for l in saida.splitlines() if "teto" in l)

    assert "VERIFICADO / NAO" in linha_rede
    assert "NAO VERIFICADO" in linha_teto
    assert linha_rede != linha_teto

    # O desfavoravel carrega instrucao; o nao-verificado NAO — senao o modelo trataria os
    # dois com o mesmo peso, que e' o defeito de origem.
    assert "FATO DESFAVORAVEL" in linha_rede
    assert "FATO DESFAVORAVEL" not in linha_teto


def test_o_fato_desfavoravel_e_nomeado_e_nao_apenas_marcado() -> None:
    """"Cite nomeando" so' funciona se o nome estiver na linha."""
    saida = render_fatos_para_prompt({"carencia_cumprida": False})
    assert "carencia cumprida" in saida
    assert "VERIFICADO / NAO" in saida


@pytest.mark.parametrize("valor", [1, 0, "sim", "nao", "", [], {}, None])
def test_nao_booleano_nunca_vira_afirmacao(valor: object) -> None:
    """`is True`/`is False` e' deliberado: `bool()` transformaria `1` e `"nao"` em fato.

    Um valor que nao seja booleano nao foi apurado por nada que este contrato reconheca, e a
    leitura segura e' NAO VERIFICADO — nunca uma afirmacao sobre o beneficiario.
    """
    saida = render_fatos_para_prompt({"beneficiario_ativo": valor})
    linha = next(l for l in saida.splitlines() if "plano ativo" in l)
    assert "NAO VERIFICADO" in linha
    assert "VERIFICADO / SIM" not in linha
    assert "VERIFICADO / NAO " not in linha


def test_todo_fato_booleano_do_contrato_aparece_mesmo_ausente() -> None:
    """Fato omitido tem de aparecer como nao-verificado, e nao sumir da lista.

    Se sumisse, o modelo nao teria como saber que ele existia — e o silencio seria lido como
    ausencia de problema.
    """
    saida = render_fatos_para_prompt({})
    for rotulo in _FATOS_BOOLEANOS.values():
        assert rotulo in saida, f"{rotulo!r} sumiu do dossie quando ausente"
    assert saida.count("NAO VERIFICADO") == len(_FATOS_BOOLEANOS)


def test_dados_que_nao_sao_booleanos_seguem_no_texto() -> None:
    """A renderizacao nomeia os booleanos e NAO descarta o resto do caso."""
    saida = render_fatos_para_prompt(
        {"codigo_procedimento_tuss": "30306027", "valor_estimado_brl": 1234.5, "rede_credenciada": True}
    )
    assert "30306027" in saida
    assert "1234.5" in saida


def test_a_instrucao_do_dossie_ensina_os_tres_estados() -> None:
    """A renderizacao sozinha nao basta: o prompt precisa dizer o que fazer com cada estado.

    Sem a regra escrita, um modelo pode continuar agrupando "NAO" e "NAO VERIFICADO" numa
    frase so' — que foi exatamente o texto que saiu.
    """
    p = dossier_prompt()
    assert "VERIFICADO / NAO" in p
    assert "NAO VERIFICADO" in p
    assert "NUNCA o omita" in p
