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
        # So' o sintoma pode ter so' a referencia interna (tabela de alarme) e so' o rascunho da fase 5 pode
        # ter so' a referencia SEM LINK: credito "Texto da operadora."
        so_sem_link = all(
            b.classificar_fonte(f.nome, f.url).classe == b.CLASSE_SEM_LINK for f in assunto.fontes
        )
        assert assunto.categoria == b.CATEGORIA_SINTOMA or (
            assunto.revisao.status == b.STATUS_RASCUNHO_PENDENTE and so_sem_link
        ), assunto.id
        assert b.linha_de_credito(assunto) == "Texto da operadora."
        return
    assert b.classificar_fonte(creditada.nome, creditada.url).classe in (
        b.CLASSE_RESPONDER,
        b.CLASSE_ATO_OFICIAL,
        b.CLASSE_LINK,
    )


# --- Rascunhos escritos por modelo (fase 5, aprovada pelo dono em 18/10/2026, so' dev) ------------------
RASCUNHOS = list(b.rascunhos_de_modelo(BASE))
#: Assuntos de saude mental: o texto termina com o CVV 188 e a oferta da equipe (README da base).
_SAUDE_MENTAL = frozenset(
    {
        "con-sindrome-de-burnout",
        "con-transtorno-de-panico",
        "con-transtorno-bipolar",
        "con-toc",
        "con-depressao-pos-parto",
    }
)


@pytest.mark.parametrize("assunto", RASCUNHOS, ids=lambda a: a.id)
def test_rascunho_de_modelo_e_marcado_e_nunca_e_sintoma(assunto: b.Assunto) -> None:
    """O rascunho diz quem o escreveu, espera a revisao medica e NUNCA cria `sintoma`: sinal de alarme so'
    vem das tabelas `triage_redflag_*` (licao do #818: "dor muito forte" isolada nao e' alarme)."""
    assert assunto.revisao.escrito_por, assunto.id
    assert assunto.revisao.revisao_medica == "pendente"
    assert assunto.tipo_texto == b.TIPO_PROPRIO, "rascunho de modelo nunca e' citacao literal"
    assert assunto.categoria != b.CATEGORIA_SINTOMA


@pytest.mark.parametrize("assunto", RASCUNHOS, ids=lambda a: a.id)
def test_rascunho_de_modelo_passa_na_checagem_de_conduta(assunto: b.Assunto) -> None:
    """A MESMA checagem que a saude gerativa aplica antes de enviar (dose, mandar usar/parar, dose esquecida,
    liberar combinacao, minimizar, diagnostico, promessa, genero, link), sobre o texto do rascunho."""
    from maezo.conhecimento import saude_gerativa as sg

    pedido = sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="", crianca=True)
    violacoes = [v.regra for v in sg.verificar(assunto.texto, pedido, cercas=lambda _t: [])]
    assert violacoes == [], f"{assunto.id}: {violacoes}"


@pytest.mark.parametrize("assunto", [a for a in RASCUNHOS if a.id in _SAUDE_MENTAL], ids=lambda a: a.id)
def test_rascunho_de_saude_mental_termina_com_o_cvv_e_a_equipe(assunto: b.Assunto) -> None:
    assert "188" in assunto.texto and "CVV" in assunto.texto, assunto.id
    assert "equipe" in assunto.texto.lower(), assunto.id
