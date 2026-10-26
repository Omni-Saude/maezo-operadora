"""Rodada de 26/10/2026: resultado de exame e duvida geral sem queixa nao entram no protocolo de sintoma.

Frases escritas para cada comportamento (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import pytest

from maezo.agents.helena.habilidades.protocolo.fluxo import duvida_sem_queixa


@pytest.mark.parametrize(
    "fala",
    [
        "Meu PSA deu fora dos parâmetros normais, o que me sugere?",
        "meu colesterol deu 260, é muito?",
        "o resultado da minha glicose veio alto",
        "o que é endometriose?",
        "pra que serve a dipirona?",
    ],
)
def test_resultado_ou_duvida_sem_queixa_fica_fora_do_protocolo(fala: str) -> None:
    assert duvida_sem_queixa(fala)


@pytest.mark.parametrize(
    "fala",
    [
        "estou com dor de cabeça",
        "estou com dor de cabeça desde ontem, o que pode ser?",
        "tô com febre e dor no corpo",
        "meu filho está com tosse",
        "minha lombar está travada",
        "minha garganta está arranhando",
        "minha barriga está estufada desde ontem",
    ],
)
def test_queixa_continua_no_protocolo(fala: str) -> None:
    assert not duvida_sem_queixa(fala)


class _V7:
    def __init__(self, tipo: str) -> None:
        self.tipo = tipo


@pytest.mark.parametrize("tipo", ["sintoma_proprio", "sintoma_de_terceiro"])
@pytest.mark.parametrize("fala", ["meu PSA deu alto", "o que é enxaqueca?", "minha pressão deu 15 por 9"])
def test_roteador_que_le_queixa_manda_ao_protocolo(fala: str, tipo: str) -> None:
    assert not duvida_sem_queixa(fala, _V7(tipo))
    assert duvida_sem_queixa(fala, _V7("duvida_geral"))


@pytest.mark.parametrize(
    "fala",
    [
        "Fui diagnosticada com mioma, é perigoso?",
        "tenho SOP, isso tem cura?",
        "meu médico disse que tenho hipotireoidismo, o que é isso?",
    ],
)
def test_duvida_do_diagnostico_vai_a_resposta_de_saude_mesmo_com_o_roteador_lendo_queixa(fala: str) -> None:
    assert duvida_sem_queixa(fala, _V7("sintoma_proprio"))


@pytest.mark.parametrize(
    "fala",
    [
        "tenho enxaqueca e estou com dor de cabeça agora",
        "fui diagnosticada com mioma e estou sangrando muito",
    ],
)
def test_diagnostico_com_queixa_continua_no_protocolo(fala: str) -> None:
    assert not duvida_sem_queixa(fala, _V7("sintoma_proprio"))
