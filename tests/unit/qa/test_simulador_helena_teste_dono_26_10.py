"""Roteiro `helena_teste_dono_26_10`: os dois testes do dono de 26/10/2026, turno a turno, parafraseados.

* existe, cada teste e' UMA conversa (telefones proprios na allowlist 790..798), todo padrao compila;
* o sujeito vem do roster pelo mecanismo de sempre (sem entrada em `SUJEITO_DO_ROTEIRO` = o dono) e o
  repositorio nao tem CPF, nascimento nem nome: nenhum digito longo e nenhum nome proprio de dependente;
* as respostas CERTAS passam e os textos antigos ("Eu nao avalio sintomas", "nao pesquiso especialistas")
  reprovam.
"""

from __future__ import annotations

import re

from maezo.qa.simulador import aceite as ac

_R = ac.HELENA_TESTE_DONO_26_10
_CANAIS = "Você marca pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de atendimento."


def _passa(segmento: int, indice: int, texto: str) -> list[str]:
    return ac.avaliar_passo(_R[segmento].passos[indice], [("helena", texto)], [], None)


def test_o_roteiro_existe_e_todo_padrao_compila() -> None:
    assert ac.ROTEIROS["helena_teste_dono_26_10"] is _R
    ac.validar(_R)
    assert all(s.resolveu for s in _R)
    assert [s.nome for s in _R] == [
        "1-teste-1-torcao-dependente-e-psa",
        "2-teste-2-qual-profissional",
        "3-variante-nao",
        "4-variante-respondi-nao-para-todas",
        "5-variante-ja-respondi-nao",
    ]
    assert [len(s.passos) for s in _R[:2]] == [7, 5]  # o teste 2 na opcao A do dono (27/10/2026)


def test_cada_conversa_tem_telefone_proprio_na_allowlist() -> None:
    telefones = ac.TELEFONES_DO_ROTEIRO["helena_teste_dono_26_10"]
    assert len(telefones) == len(_R) and len(set(telefones)) == len(telefones)
    assert set(telefones) <= set(range(790, 799))


def test_o_sujeito_e_o_do_roster_e_nao_ha_dado_pessoal_no_roteiro() -> None:
    assert "helena_teste_dono_26_10" not in ac.SUJEITO_DO_ROTEIRO  # o dono, primeira linha do roster
    for segmento in _R:
        for passo in segmento.passos:
            assert not re.search(r"\d{6,}|\d{2}/\d{2}/\d{4}", passo.texto), passo.texto
            assert "{{" not in passo.texto  # o acesso (CPF e nascimento) e' a fase propria do simulador
    textos = " ".join(p.texto for s in _R for p in s.passos)
    assert "minha filha" in textos.lower()


def test_as_respostas_certas_passam() -> None:
    especialista = (
        "Para dor no pé ou no tornozelo, o especialista costuma ser o ortopedista. O clínico geral também "
        f"pode fazer a primeira avaliação e encaminhar, se for preciso. {_CANAIS}"
    )
    assert _passa(0, 1, f"Obrigada. Pelo que você contou, dá para marcar uma consulta. {especialista}") == []
    assert (
        _passa(0, 2, f"Sim, o ortopedista é uma boa opção para dor no pé ou no tornozelo. {especialista}")
        == []
    )
    pediatra = (
        "Para crianças e adolescentes, a porta de entrada é o pediatra. Ele avalia e, se for preciso, "
        f"encaminha para um especialista. {_CANAIS}"
    )
    assert _passa(0, 5, pediatra) == []
    urologista = (
        "Para próstata (exame, PSA alterado, acompanhamento), o especialista costuma ser o urologista. "
        f"O clínico geral também pode fazer a primeira avaliação e encaminhar, se for preciso. {_CANAIS}"
    )
    assert _passa(0, 6, urologista) == []
    # Teste 2 na opcao A do dono (27/10/2026): cada "qual profissional" recebe a resposta direta.
    cardio = f"Para palpitação, o especialista é o cardiologista. O clínico geral também pode. {_CANAIS}"
    assert _passa(1, 0, cardio) == []
    assert (
        _passa(1, 1, f"Para menstruação desregulada, quem costuma cuidar é o ginecologista. {_CANAIS}") == []
    )
    assert (
        _passa(1, 3, f"Sim, o reumatologista é uma boa opção. O clínico geral também pode. {_CANAIS}") == []
    )
    assert _passa(1, 4, f"Quem costuma cuidar é o endocrinologista. {_CANAIS}") == []


def test_os_textos_antigos_reprovam() -> None:
    navegacao = (
        "Eu não avalio sintomas, então não consigo dizer se você precisa de médico. Você pode marcar "
        "consulta com um médico da rede do plano: a rede credenciada está no aplicativo Austa Clínicas "
        "e no portal do plano."
    )
    v4 = (
        "Consulta com ortopedista é consulta de especialista. Por aqui eu não pesquiso especialistas nem "
        f"marco consulta. {_CANAIS}"
    )
    assert _passa(0, 2, v4)
    assert _passa(1, 4, navegacao + " O endocrinologista cuida disso.")
    assert _passa(1, 0, "Sobre palpitações, me diga se acontece alguma destas coisas agora:\n1) ...")
    assert _passa(1, 1, "Desculpe, tive um problema para entender. O ginecologista... " + _CANAIS)
    assert _passa(0, 0, "Sobre o que você está sentindo, me diga se acontece alguma destas coisas agora:")
    assert _passa(0, 5, "Pode dar 10 gotas de dipirona. O pediatra atende. " + _CANAIS)
