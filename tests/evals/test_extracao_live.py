"""Frente 3.3 — medir a TRADUCAO ao vivo, campo a campo, contra o corpus rotulado.

O QUE ISTO MEDE E OS OUTROS EVALS NAO. Os conjuntos existentes rodam com RESPOSTA GRAVADA: testam
o grafo dado um resultado, nao se o modelo acerta o resultado. O Tier B de `test_classifier_evals`
re-executa o `classify` ao vivo, mas pontua contra o proprio `recorded_llm` — a linha de base e' o
que o modelo disse UMA VEZ, nao o que ele DEVERIA dizer. Nenhum dos dois consegue pegar o defeito de
13/09, em que "dor de cabeca" virou `cefaleia_subita_intensa` e abriu P1 de cinco minutos.

Este arquivo pontua contra um rotulo HUMANO (`tests/evals/extracao/casos.json`, a Frente 3.2), que e'
a unica linha de base capaz de dizer que o modelo errou.

CAMPO A CAMPO, e nao "acertou o caso", porque a regua tem cinco regras e a medida precisa dizer QUAL
esta sendo violada. Um agregado que cai de 0,94 para 0,88 nao diz se a extracao passou a inventar
intensidade ou a errar a populacao — e sao problemas com gravidades diferentes.

OS PESOS NAO SAO IGUAIS, e a diferenca e' clinica, nao estatistica: errar `population` erra a TABELA
INTEIRA de uma vez, e errar `sintoma_codigo` e' literalmente o defeito de 13/09. As idades e a
intensidade tem caminho de recuperacao (a coleta pergunta); a populacao nao tem.

ESTE TESTE NAO E' BLOQUEANTE DE PR — e' Tier B, nightly, e pula alto sem chave. O documento pede
"rodar na esteira, com limiar que barra a entrega", e barrar a entrega com um limiar que ninguem
calibrou contra trafego real seria uma cerca que reprova o certo. O caminho honesto e' medir
primeiro, publicar o numero, e so' depois promover a bloqueante — o que esta registrado como
pendencia no `docs/design/regua-de-extracao.md`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Final

import pytest

from maezo.agents.helena.graph import HelenaGraph, HelenaState
from maezo.runtime.inference import InferenceProvider, InferenceSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from .conftest import FakeWhatsAppSender, live_key_skip

_CORPUS: Final[Path] = Path(__file__).resolve().parent / "extracao" / "casos.json"

#: Peso por campo. Ver a tabela em `docs/design/regua-de-extracao.md` — os numeros aqui e la' sao a
#: MESMA decisao, e o teste abaixo (`test_os_pesos_estao_documentados`) impede que um mude sem o
#: outro. Um peso ajustado em silencio e' a forma mais barata de fazer uma nota subir.
_PESOS: Final[dict[str, float]] = {
    "intent": 3.0,  # decide se a DMN roda
    "population": 3.0,  # erra a tabela inteira de uma vez
    "sintoma_codigo": 3.0,  # o defeito de 13/09
    "psychosocial_risk": 3.0,  # gatilho 5, sempre avaliado, sempre escala
    "intensidade": 1.0,  # entra na DMN, mas a rede de seguranca ainda pega o `grave`
    "idade_anos": 1.0,
    "idade_meses": 1.0,
    "idade_gestacional_semanas": 1.0,
}

#: Limiar DE PARTIDA, herdado do que o harness ja usa para classificacao (`live.threshold` = 0.9).
#: NAO E' UM NUMERO MEDIDO — esta' registrado como pendencia de calibracao na regua. Um limiar
#: escolhido no escritorio reprova o certo ou aprova o errado; este existe para que a primeira
#: medicao tenha contra o que ser comparada, nao para autorizar entrega.
_LIMIAR_DE_PARTIDA: Final[float] = 0.90


def _casos() -> list[dict[str, Any]]:
    return list(json.loads(_CORPUS.read_text(encoding="utf-8"))["casos"])


def _estado(mensagem: str) -> HelenaState:
    return {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_corpus",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-corpus",
        "message_body": mensagem,
    }


def _grafo() -> HelenaGraph:
    """Um grafo com o modelo REAL e todo o resto falso.

    A DMN e' um duplo VAZIO de proposito: se `classify` chegar a consulta-la, o duplo levanta e o
    teste falha alto. Aqui se mede a TRADUCAO — o que o modelo extraiu —, nunca a conduta que a
    tabela deriva dela; misturar as duas coisas produziria uma nota que sobe quando a tabela muda.
    """
    return HelenaGraph(
        inference=InferenceProvider(settings=InferenceSettings(provider="anthropic")),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=FakeWhatsAppSender(),
        memoria_clinica_enabled=False,  # cada caso do corpus e' rotulado como mensagem SOZINHA
    )


# =================================================================================================
# Cercas deterministicas — rodam sempre, sem chave e sem rede
# =================================================================================================


def test_os_pesos_cobrem_exatamente_os_campos_rotulados() -> None:
    """Um campo rotulado e sem peso nao entra na nota — some da medicao sem ninguem ver.

    E um peso para campo que ninguem rotula e' um denominador inflado, que faz a nota parecer
    melhor do que e'.
    """
    campos_do_corpus = {campo for caso in _casos() for campo in caso["esperado"]}
    assert set(_PESOS) == campos_do_corpus, (
        f"pesos e rotulos divergem: so' nos pesos={sorted(set(_PESOS) - campos_do_corpus)}, "
        f"so' no corpus={sorted(campos_do_corpus - set(_PESOS))}"
    )


def test_os_pesos_estao_documentados_na_regua() -> None:
    """O peso e' uma decisao clinica disfarcada de constante.

    Se ele puder mudar aqui sem mudar la', a forma mais barata de fazer a nota subir passa a ser
    baixar o peso do campo que erra — e ninguem le' um diff de constante como decisao.
    """
    regua = (Path(__file__).resolve().parents[2] / "docs" / "design" / "regua-de-extracao.md").read_text(
        encoding="utf-8"
    )
    for campo, peso in _PESOS.items():
        if peso >= 3.0:
            assert campo in regua, f"campo de peso alto {campo!r} nao aparece na regua"
    assert "alto" in regua and "médio" in regua, "a tabela de pesos sumiu da regua"


def test_o_corpus_e_carregavel_e_nao_encolheu() -> None:
    """Prova de nao-vacuidade: um corpus vazio faria todo teste ao vivo abaixo passar sem medir."""
    casos = _casos()
    assert len(casos) >= 100, f"o corpus tem {len(casos)} casos — a medicao ao vivo ficaria fraca"


# =================================================================================================
# Medicao ao vivo — Tier B, nightly, pula alto sem chave
# =================================================================================================


def _pontuar(obtido: dict[str, Any], esperado: dict[str, Any]) -> tuple[float, dict[str, bool]]:
    """Nota ponderada de UM caso, mais o acerto por campo.

    `obtido` e' o retorno de `_classify_llm`, que ja' passou pela validacao de esquema do grafo —
    entao um valor fora do vocabulario chega aqui como caso invalido, nao como acerto parcial.
    """
    acertos: dict[str, bool] = {}
    total = 0.0
    peso_total = 0.0
    for campo, peso in _PESOS.items():
        acertou = obtido.get(campo, "<ausente>") == esperado[campo]
        acertos[campo] = acertou
        peso_total += peso
        total += peso if acertou else 0.0
    return (total / peso_total if peso_total else 1.0), acertos


@live_key_skip
@pytest.mark.llm_live
@pytest.mark.eval
async def test_extracao_ao_vivo_contra_o_corpus_rotulado() -> None:
    """Roda o `classify` REAL contra os 116 casos e publica a nota por campo e por regra.

    A SAIDA IMPORTA TANTO QUANTO O VEREDITO: um numero agregado nao diz o que consertar. O relatorio
    abaixo sai no stdout do teste e e' o que se leva para a conversa com quem assina a regua —
    "a R2 esta em 0,71" e' uma frase acionavel; "a extracao esta em 0,89" nao e'.
    """
    grafo = _grafo()
    casos = _casos()

    notas: list[float] = []
    por_campo: dict[str, list[bool]] = defaultdict(list)
    por_regra: dict[str, list[float]] = defaultdict(list)
    falhas: list[str] = []

    for caso in casos:
        extracao, falha = await grafo._classify_llm(_estado(caso["mensagem"]))
        if extracao is None:
            # Extracao INVALIDA conta como zero, nunca como caso pulado: em producao isto vira
            # `falha_tecnica` e o turno inteiro cai em humano. Pular baixaria o denominador e faria
            # um modelo que falha muito parecer um modelo que acerta muito.
            notas.append(0.0)
            falhas.append(f"{caso['id']}: extracao invalida ({falha})")
            for campo in _PESOS:
                por_campo[campo].append(False)
            for regra in caso["regras"]:
                por_regra[regra].append(0.0)
            continue

        nota, acertos = _pontuar(extracao, caso["esperado"])
        notas.append(nota)
        for campo, ok in acertos.items():
            por_campo[campo].append(ok)
        for regra in caso["regras"]:
            por_regra[regra].append(nota)
        if nota < 1.0:
            errados = {c: (extracao.get(c), caso["esperado"][c]) for c, ok in acertos.items() if not ok}
            falhas.append(f"{caso['id']}: {errados}")

    agregada = sum(notas) / len(notas)

    print("\n=== EXTRACAO AO VIVO (Frente 3.3) ===")
    print(f"casos: {len(casos)}   nota agregada (ponderada): {agregada:.3f}")
    print("\npor campo:")
    for campo in sorted(_PESOS, key=lambda c: -_PESOS[c]):
        marcas = por_campo[campo]
        print(f"  {campo:30s} peso {_PESOS[campo]:.0f}  {sum(marcas) / len(marcas):.3f}")
    print("\npor regra da regua:")
    for regra in sorted(por_regra):
        vals = por_regra[regra]
        print(f"  {regra}  ({len(vals):3d} casos)  {sum(vals) / len(vals):.3f}")
    if falhas:
        print(f"\n{len(falhas)} caso(s) com erro:")
        for linha in falhas[:40]:
            print(f"  {linha}")
        if len(falhas) > 40:
            print(f"  ... e mais {len(falhas) - 40}")

    assert agregada >= _LIMIAR_DE_PARTIDA, (
        f"extracao em {agregada:.3f}, abaixo do limiar de partida {_LIMIAR_DE_PARTIDA:.2f}. "
        f"LEIA O RELATORIO ACIMA antes de mexer no limiar: ele diz qual CAMPO e qual REGRA estao "
        f"caindo. Baixar o limiar porque a nota caiu e' transformar a regua numa fita metrica — se "
        f"o numero estiver errado, o caminho e' recalibra-lo com medicao registrada, nunca ajusta-lo "
        f"para caber no resultado do dia."
    )
