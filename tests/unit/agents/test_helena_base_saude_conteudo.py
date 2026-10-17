"""GATE DE CI do CONTEUDO da base de conhecimento de saude (ADR-0064, DL-0118).

Carrega TODA a base real (`spec/conhecimento/saude`) e reprova o PR se: o schema e' invalido (inclui fonte
sem url/licenca/coletado_em, texto acima de 700 caracteres e sinonimo duplicado entre ids — o carregador
recusa a base inteira), `revisar_ate` venceu, alguma fonte esta' na lista NAO USAR ou fora da lista fechada,
`citacao_literal` nao e' de CC BY-ND/ato oficial, o texto tem termo proibido (dose, mg, "tome", "pode tomar",
"nao e' grave", "e' normal"...), alguma frase passa de 20 palavras (regua de tom) ou alguma resposta montada
da base e' barrada pelas cercas de saida da Helena (recusa, canal nao confirmado, anuncio de encaminhamento).

A base e' escrita por outra frente e entra por PR; enquanto o diretorio so' tem o `FONTES.md`, os testes que
dependem de assunto ficam vazios (o carregamento continua conferido).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.conhecimento import base_saude as b
from maezo.qa.simulador.checagens import frases_longas

_RAIZ = Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "saude"
_HOJE = datetime.now(UTC).date()


def _base() -> b.BaseSaude:
    return b.carregar_base(_RAIZ)


BASE = _base()
ASSUNTOS = sorted(BASE.assuntos.values(), key=lambda a: a.id)


def test_a_base_real_carrega_sem_erro_de_schema() -> None:
    """Schema, lugar do arquivo, id = nome do arquivo, ids e sinonimos unicos (o carregador reprova tudo)."""
    assert (_RAIZ / "FONTES.md").is_file(), "a lista fechada de fontes mora ao lado da base"
    _base()


@pytest.mark.parametrize("assunto", ASSUNTOS, ids=lambda a: a.id)
def test_cada_assunto_passa_na_politica_de_conteudo(assunto: b.Assunto) -> None:
    problemas = b.problemas_de_politica(assunto, hoje=_HOJE)
    assert problemas == [], f"{assunto.id}: {problemas}"


def _respostas(assunto: b.Assunto) -> list[str]:
    respostas = [b.resposta_informativa(assunto), b.resposta_de_resultado_pessoal(assunto)]
    if assunto.categoria == b.CATEGORIA_SINTOMA:
        respostas.append(b.texto_final_do_sintoma_com_orientacao("o que você me contou", assunto))
    return respostas


@pytest.mark.parametrize("assunto", ASSUNTOS, ids=lambda a: a.id)
def test_cada_resposta_da_base_passa_nas_cercas_de_saida_e_na_regua_de_tom(assunto: b.Assunto) -> None:
    for texto in _respostas(assunto):
        assert motivo_de_recusa(texto, "inform", start_aconteceu=None) is None, f"{assunto.id}: recusa"
        assert motivo_de_canal_nao_confirmado(texto) is None, f"{assunto.id}: canal nao confirmado"
        assert menciona_encaminhamento(texto) is False, f"{assunto.id}: anuncia encaminhamento"
        assert frases_longas(texto) == [], f"{assunto.id}: frase com mais de 20 palavras"
    if assunto.perguntas_alerta:
        pergunta = " ".join(assunto.perguntas_alerta)
        assert frases_longas(pergunta) == [], f"{assunto.id}: pergunta de alerta com mais de 20 palavras"
        assert motivo_de_recusa(pergunta, "collect", start_aconteceu=None) is None


@pytest.mark.parametrize("assunto", ASSUNTOS, ids=lambda a: a.id)
def test_o_texto_de_cada_assunto_respeita_o_limite_e_a_fonte_creditada_responde(assunto: b.Assunto) -> None:
    assert len(assunto.texto) <= b.LIMITE_DO_TEXTO
    creditada = b.fonte_creditada(assunto)
    if creditada is None:
        # So' o sintoma pode ter so' a referencia interna (tabela de alarme): credito "Texto da operadora."
        assert assunto.categoria == b.CATEGORIA_SINTOMA, assunto.id
        return
    assert b.classificar_fonte(creditada.nome, creditada.url).classe in (
        b.CLASSE_RESPONDER,
        b.CLASSE_ATO_OFICIAL,
        b.CLASSE_LINK,
    )
