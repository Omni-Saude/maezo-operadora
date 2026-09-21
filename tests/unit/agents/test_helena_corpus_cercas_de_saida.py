"""A CERCA DE NAO-REGRESSAO das tres cercas de saida da Helena (21/09/2026, terceira rodada).

POR QUE ESTE ARQUIVO EXISTE, e o que ele conserta no METODO e nao no codigo.

O achado CRITICO 2 da terceira rodada de review foi uma TROCA SILENCIOSA DE VEREDITO: a rodada 2
relaxou `motivo_de_canal_nao_confirmado` (a referencia de volta) para consertar um falso positivo
real, e no mesmo movimento passou a APROVAR um nome de canal INVENTADO desde que o texto citasse
qualquer canal confirmado em outro lugar. Toda a suite continuou verde, porque nenhum teste fixava
o veredito daquela FAMILIA de textos — os testes fixavam os textos que a rodada 2 tinha em maos.

E' o mesmo defeito de metodo pela terceira vez: a cerca muda, e o que se mede e' o caso novo.

O QUE ESTE ARQUIVO FAZ: fixa, num arquivo VERSIONADO (`corpus_cercas_de_saida.json`), o veredito
das tres cercas para TODOS os textos medidos nas tres rodadas — a bateria do diretor, os
adversariais da rodada 2, os textos do reviewer na rodada 3 e as constantes que vao literalmente
ao beneficiario. O corpus roda inteiro, texto a texto.

A CONSEQUENCIA E' O PONTO: mudar o veredito de um texto passa a exigir EDITAR O CORPUS. Nao ha
como afrouxar uma cerca "de passagem" — a mudanca aparece no diff como uma declaracao caso a caso,
com o `origem` de cada texto do lado, que e' o que faltou nas duas rodadas anteriores. Uma troca de
veredito DELIBERADA continua possivel (e ha' uma nesta rodada: "o portal mostra a mesma lista",
declarada em `test_helena_canais_confirmados.py`); o que deixa de ser possivel e' a troca que
ninguem viu.

O QUE ELE NAO E': nao e' substituto dos arquivos por assunto. Eles explicam POR QUE cada veredito
e' aquele; este so' garante que nenhum deles muda sem que alguem diga que sim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena import prompts as prompts_module
from maezo.agents.helena.graph import _PERGUNTA_FALLBACK
from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)

_CORPUS = Path(__file__).parent / "corpus_cercas_de_saida.json"

#: O minimo declarado. Nao e' uma meta de cobertura: e' o tamanho abaixo do qual o corpus deixaria
#: de conter os textos das tres rodadas, e portanto deixaria de ser uma cerca de nao-regressao.
TAMANHO_MINIMO = 60


def _entradas() -> list[dict[str, Any]]:
    dados = json.loads(_CORPUS.read_text(encoding="utf-8"))
    return list(dados["entradas"])


ENTRADAS = _entradas()


def _id(entrada: dict[str, Any]) -> str:
    return f"{entrada['rota']}/{entrada['start_aconteceu']}/{entrada['texto'][:60]}"


def test_o_corpus_tem_os_textos_das_tres_rodadas() -> None:
    """Nao-vacuidade: um corpus vazio (ou esvaziado) tornaria todo este arquivo verde por nada.

    A contagem por ORIGEM entra na assercao porque as tres rodadas tem de estar representadas —
    um corpus com so' os textos da rodada 3 seria exatamente o defeito de metodo que ele existe
    para consertar.
    """
    assert len(ENTRADAS) >= TAMANHO_MINIMO, f"o corpus encolheu para {len(ENTRADAS)} entradas"

    origens = [str(e["origem"]) for e in ENTRADAS]
    assert any("bateria do diretor" in o for o in origens), "nenhum texto da bateria do diretor"
    assert any("rodada 2" in o for o in origens), "nenhum texto dos adversariais da rodada 2"
    assert any("rodada 3" in o for o in origens), "nenhum texto medido pelo reviewer na rodada 3"
    assert any("constante" in o for o in origens), "nenhuma constante enviada ao beneficiario"


def test_cada_entrada_do_corpus_e_unica_e_declarada() -> None:
    """Entrada duplicada esconde uma troca de veredito: a segunda copia fica verde pela primeira.

    E `origem` e' OBRIGATORIA — um texto sem procedencia no corpus e' um texto que ninguem sabe
    por que esta' la', e daqui a duas rodadas ele e' apagado por parecer ruido.
    """
    chaves = [(e["texto"], e["rota"], e["start_aconteceu"]) for e in ENTRADAS]
    duplicadas = {c for c in chaves if chaves.count(c) > 1}

    assert not duplicadas, f"entradas duplicadas no corpus: {sorted(str(d) for d in duplicadas)}"

    sem_origem = [e["texto"][:60] for e in ENTRADAS if not str(e.get("origem", "")).strip()]
    assert not sem_origem, f"entradas sem `origem` declarada: {sem_origem}"


@pytest.mark.parametrize("entrada", ENTRADAS, ids=_id)
def test_o_veredito_das_tres_cercas_nao_mudou(entrada: dict[str, Any]) -> None:
    """O corpus inteiro, texto a texto, contra as tres cercas REAIS.

    As tres respostas sao independentes e as tres importam:

      * `motivo_de_recusa` — o texto pode sair nesta rota, com este fato de start?
      * `motivo_de_canal_nao_confirmado` — ele manda alguem para um lugar que existe?
      * `menciona_encaminhamento` — ele AVISA que um humano assumiu? (e' o gatilho dos dois lados
        da cerca TEXTO x FATO, entao um veredito trocado aqui muda dois comportamentos)

    A mensagem de falha carrega o `origem` de proposito: quem quebrar isto precisa saber QUAL
    medicao esta' contradizendo antes de decidir editar o corpus.
    """
    texto = str(entrada["texto"])
    rota = str(entrada["rota"])
    start = entrada["start_aconteceu"]
    esperado = entrada["esperado"]
    contexto = f"origem={entrada['origem']!r} texto={texto[:80]!r}"

    recusa = motivo_de_recusa(texto, rota, start_aconteceu=start)
    grupo_recusa = recusa[0] if recusa is not None else None
    assert grupo_recusa == esperado["recusa"], f"`motivo_de_recusa` mudou de veredito — {contexto}"

    canal = motivo_de_canal_nao_confirmado(texto)
    grupo_canal = canal[0] if canal is not None else None
    assert grupo_canal == esperado["canal"], (
        f"`motivo_de_canal_nao_confirmado` mudou de veredito — {contexto}"
    )

    assert menciona_encaminhamento(texto) is esperado["menciona"], (
        f"`menciona_encaminhamento` mudou de veredito — {contexto}"
    )


def test_toda_constante_enviada_ao_beneficiario_esta_no_corpus() -> None:
    """A DIRECAO QUE PEGA A CONSTANTE NOVA.

    Uma frase que o repositorio envia LITERALMENTE a um beneficiario e' a ultima que pode estar
    fora do corpus: ela nao passa por modelo nenhum, entao um veredito errado nela e' 100% dos
    turnos daquele caminho — e foi assim que a frase de conformidade ditada pelo dono quase saiu
    casando a propria cerca de capacidade (21/09/2026, primeira rodada).

    A varredura e' por REFLEXAO (`RESPOSTA_*` dos dois modulos + as perguntas de fallback da
    coleta), e nao por uma lista a mao, porque uma lista a mao envelhece exatamente como o
    comentario que ela substituiria.
    """
    textos_no_corpus = {str(e["texto"]) for e in ENTRADAS}

    constantes: dict[str, str] = {}
    for modulo in (graph_module, prompts_module):
        for nome, valor in vars(modulo).items():
            if nome.startswith("RESPOSTA_") and isinstance(valor, str):
                constantes[f"{modulo.__name__}.{nome}"] = valor
    for chave, pergunta in _PERGUNTA_FALLBACK.items():
        constantes[f"_PERGUNTA_FALLBACK[{chave!r}]"] = pergunta

    assert constantes, "a reflexao nao achou constante nenhuma — a cerca ficaria vacua"

    faltando = sorted(nome for nome, valor in constantes.items() if valor not in textos_no_corpus)
    assert not faltando, (
        "constante enviada ao beneficiario e AUSENTE do corpus de nao-regressao "
        f"(tests/unit/agents/corpus_cercas_de_saida.json): {faltando}"
    )
