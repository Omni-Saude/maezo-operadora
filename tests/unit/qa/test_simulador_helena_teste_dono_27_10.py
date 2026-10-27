"""Roteiro `helena_teste_dono_27_10`: o teste do dono de 27/10/2026, parafraseado, na opcao A dele.

* existe, um assunto por conversa (telefones proprios na allowlist), todo padrao compila, sem dado pessoal;
* as respostas CERTAS passam (especialista direto, sem perguntas; seguimento sem 192; contestacao sem CVV);
* as respostas do teste do dono reprovam (perguntas de alerta, 192 + caso, "nao pesquiso", CVV).
"""

from __future__ import annotations

import re

from maezo.qa.simulador import aceite as ac

_R = ac.HELENA_TESTE_DONO_27_10
_CANAIS = "Você marca pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de atendimento."
_URGENTE = (
    "Ligue 192 (SAMU) agora ou vá ao pronto-socorro. Não dirija: peça para alguém te levar ou chame o SAMU."
)


def _passa(segmento: int, indice: int, texto: str) -> list[str]:
    return ac.avaliar_passo(_R[segmento].passos[indice], [("helena", texto)], [], None)


def test_o_roteiro_existe_e_todo_padrao_compila() -> None:
    assert ac.ROTEIROS["helena_teste_dono_27_10"] is _R
    ac.validar(_R)
    assert all(s.resolveu for s in _R)
    assert len(_R) == 6 and [len(s.passos) for s in _R] == [3, 2, 2, 1, 1, 1]


def test_cada_conversa_tem_telefone_proprio_na_allowlist_e_nao_ha_dado_pessoal() -> None:
    telefones = ac.TELEFONES_DO_ROTEIRO["helena_teste_dono_27_10"]
    assert len(telefones) == len(_R) and len(set(telefones)) == len(telefones)
    assert set(telefones) <= set(range(790, 799))
    assert "helena_teste_dono_27_10" not in ac.SUJEITO_DO_ROTEIRO
    for segmento in _R:
        for passo in segmento.passos:
            assert not re.search(r"\d{6,}|\d{2}/\d{2}/\d{4}", passo.texto), passo.texto


def test_as_respostas_certas_passam() -> None:
    peito = (
        "Para dor no peito, o ideal é ser avaliado ainda hoje, num pronto-atendimento ou numa consulta no "
        "mesmo "
        "dia. Para dor ou aperto no peito que vem e vai, o especialista costuma ser o cardiologista. O "
        "clínico "
        f"geral também pode fazer a primeira avaliação. {_CANAIS}\n\nSe a dor vier com suor frio, ligue 192."
    )
    assert _passa(0, 0, peito) == []
    assert _passa(0, 1, f"Sim, o cardiologista é uma boa opção. {_CANAIS}") == []
    assert _passa(1, 0, f"Para jato fraco, o especialista costuma ser o urologista. {_CANAIS}") == []
    libido = (
        "Para cansaço constante, baixa energia ou perda de libido, quem costuma cuidar é o urologista (para "
        f"homens), o ginecologista (para mulheres) ou o endocrinologista, conforme o caso. {_CANAIS}"
    )
    assert _passa(2, 0, libido) == []
    assert _passa(2, 1, f"Desculpe, eu entendi errado. {libido}") == []


def test_as_respostas_do_teste_do_dono_reprovam() -> None:
    perguntas = "Sobre dor no peito, me diga se acontece alguma destas coisas agora:\n1) A dor é de aperto?"
    assert _passa(0, 0, perguntas)
    assert _passa(0, 1, _URGENTE)
    v4 = (
        "Consulta com urologista é consulta de especialista. Por aqui eu não pesquiso especialistas nem "
        "marco "
        f"consulta. {_CANAIS}"
    )
    assert _passa(1, 1, v4)
    cvv = (
        "Sinto muito que você esteja se sentindo assim. Se pensar em se machucar, ligue para o CVV no 188. "
        f"Para depressão, o psiquiatra. {_CANAIS}"
    )
    assert _passa(2, 0, cvv)
    assert _passa(2, 1, "Desculpe, não entendi bem. Só para confirmar, isto acontece agora?")
