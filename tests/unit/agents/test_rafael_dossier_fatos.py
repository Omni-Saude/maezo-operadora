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

SEGUNDO DEFEITO, do proprio conserto (medido em 25/08/2026 e corrigido no mesmo dia)

A primeira versao da renderizacao marcava o fato desfavoravel com
`[FATO DESFAVORAVEL: cite nomeando]` no fim da linha. Em 4 casos rodados no ambiente, 1
VAZOU: o modelo copiou a frase em caixa alta para a narrativa —
"FATO DESFAVORAVEL: beneficiario sem plano ativo". Um artefato de sistema num documento que
um medico le, e o diretor pegou.

Licao que estes testes travam: marcacao que PARECE frase pronta convida a ser reproduzida.
Marcacao e' token curto (`SIM` / `NAO` / `SEM DADO`), a regra mora no prompt, e o prompt
proibe explicitamente copiar a marcacao.

Estes testes nao testam o modelo. Testam que o modelo recebe tres formas distinguiveis e uma
instrucao de nao as copiar — que e' a parte deterministica.
"""

from __future__ import annotations

import pytest

from maezo.agents.rafael.graph import _FATOS_BOOLEANOS, render_fatos_para_prompt
from maezo.agents.rafael.prompts import dossier_prompt


def _linha(saida: str, trecho: str) -> str:
    return next(linha for linha in saida.splitlines() if trecho in linha)


def test_apurado_negativo_e_sem_dado_nao_se_parecem() -> None:
    """O caso exato que falhou: rede verificada-e-falsa ao lado de teto sem valor."""
    saida = render_fatos_para_prompt({"rede_credenciada": False, "dentro_teto_l2": None})

    rede = _linha(saida, "rede credenciada")
    teto = _linha(saida, "teto")

    assert rede.strip().startswith("NAO")
    assert teto.strip().startswith("SEM DADO")
    assert rede != teto


def test_a_marcacao_nao_carrega_instrucao_para_o_modelo_copiar() -> None:
    """O segundo defeito, travado: nada na linha deve parecer frase pronta.

    `[FATO DESFAVORAVEL: cite nomeando]` foi copiado verbatim para a narrativa em 1 de 4
    casos. A instrucao pertence ao prompt; a linha carrega so' o estado.
    """
    saida = render_fatos_para_prompt(dict.fromkeys(_FATOS_BOOLEANOS, False))

    assert "[" not in saida and "]" not in saida, "colchete na linha convida a ser copiado"
    assert "DESFAVORAVEL" not in saida
    assert "cite" not in saida.lower()


def test_o_prompt_proibe_reproduzir_a_marcacao() -> None:
    """Sem esta frase o modelo trata a marcacao como texto — foi o que aconteceu."""
    p = dossier_prompt()
    assert "MARCACAO E' INTERNA" in p
    assert "Escreva prosa" in p


def test_o_prompt_proibe_a_frase_que_apagava_o_fato() -> None:
    """A terceira camada, e a que ataca o defeito de origem pela LINGUAGEM.

    Medido em 25/08 depois de tirar o vazamento do marcador: em 1 de 4 casos o modelo ainda
    escreveu "nao ha registro de que o prestador esteja na rede credenciada" para um fato
    apurado como falso. E' a construcao que o dossie original usava — a que faz o medico
    conferir em vez de ler um motivo de negativa.

    Marcacao distinguivel e regra dos tres estados nao bastaram: era preciso PROIBIR a frase
    e dar a alternativa pronta. Este teste trava as duas metades.
    """
    p = dossier_prompt()
    assert "FRASE PROIBIDA" in p
    assert "nao ha registro" in p
    assert "nao esta na rede" in p, "a alternativa correta precisa vir junto da proibicao"


def test_o_fato_desfavoravel_e_nomeado_e_nao_apenas_marcado() -> None:
    """Nomear so' funciona se o nome do fato estiver na linha."""
    saida = render_fatos_para_prompt({"carencia_cumprida": False})
    assert "carencia cumprida" in saida
    assert _linha(saida, "carencia cumprida").strip().startswith("NAO")


@pytest.mark.parametrize("valor", [1, 0, "sim", "nao", "", [], {}, None])
def test_nao_booleano_nunca_vira_afirmacao(valor: object) -> None:
    """`is True`/`is False` e' deliberado: `bool()` transformaria `1` e `"nao"` em fato.

    Um valor que nao seja booleano nao foi apurado por nada que este contrato reconheca, e a
    leitura segura e' SEM DADO — nunca uma afirmacao sobre o beneficiario.
    """
    saida = render_fatos_para_prompt({"beneficiario_ativo": valor})
    assert _linha(saida, "plano ativo").strip().startswith("SEM DADO")


def test_todo_fato_booleano_do_contrato_aparece_mesmo_ausente() -> None:
    """Fato omitido tem de aparecer como sem-dado, e nao sumir da lista.

    Se sumisse, o modelo nao teria como saber que ele existia — e o silencio seria lido como
    ausencia de problema.
    """
    saida = render_fatos_para_prompt({})
    for rotulo in _FATOS_BOOLEANOS.values():
        assert rotulo in saida, f"{rotulo!r} sumiu do dossie quando ausente"
    assert saida.count("SEM DADO") == len(_FATOS_BOOLEANOS)


def test_os_tres_estados_convivem_no_mesmo_caso() -> None:
    """O caso real tem os tres ao mesmo tempo, e e' ai' que a confusao aparecia."""
    saida = render_fatos_para_prompt(
        {"beneficiario_ativo": True, "rede_credenciada": False, "dentro_teto_l2": None}
    )
    assert _linha(saida, "plano ativo").strip().startswith("SIM")
    assert _linha(saida, "rede credenciada").strip().startswith("NAO")
    assert _linha(saida, "teto").strip().startswith("SEM DADO")


def test_dados_que_nao_sao_booleanos_seguem_no_texto() -> None:
    """A renderizacao nomeia os booleanos e NAO descarta o resto do caso."""
    saida = render_fatos_para_prompt(
        {"codigo_procedimento_tuss": "30306027", "valor_estimado_brl": 1234.5, "rede_credenciada": True}
    )
    assert "30306027" in saida
    assert "1234.5" in saida
