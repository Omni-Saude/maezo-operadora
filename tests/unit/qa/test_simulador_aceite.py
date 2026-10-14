"""Roteiro de aceite do dono: casamento (sem acento/caixa), assercoes por passo e o executor."""

from __future__ import annotations

import pytest

from maezo.qa.simulador import aceite as ac
from maezo.qa.simulador.registro import Turno

from .apoio_simulador import CPF_SINTETICO, escalonamento, sujeito, turno


def _falhas(
    passo: ac.Passo,
    *respostas: tuple[str, str],
    esc: list[dict[str, str | None]] | None = None,
    anterior: str | None = None,
) -> list[str]:
    return ac.avaliar_passo(passo, list(respostas), esc or [], anterior)


# --- casamento ------------------------------------------------------------------------------------
def test_casamento_sem_acento_e_sem_caixa_no_texto_e_no_padrao() -> None:
    passo = ac.Passo("x", deve=("carência", "2ª via", "OBSTETR"))
    assert (
        _falhas(passo, ("lucas", "Sua CARENCIA acabou. Segunda: 2a via do boleto. Obstetrícia coberta."))
        == []
    )
    assert _falhas(passo, ("lucas", "nada")) != []


def test_deve_exige_todos_e_nao_deve_nenhum() -> None:
    passo = ac.Passo(
        "x", deve=("empresa contratante", "julho de 2026"), nao_deve=("nao tenho outra informa",)
    )
    ok = ("lucas", "A empresa contratante pagou a de julho de 2026.")
    assert _falhas(passo, ok) == []
    (falha,) = _falhas(passo, ("lucas", "A empresa contratante pagou."))
    assert falha.startswith("deve") and "julho de 2026" in falha
    (falha,) = _falhas(passo, ok, ("helena", "Não tenho outra informação."))
    assert falha.startswith("nao_deve") and "nao tenho outra informa" in falha


def test_circunflexo_e_alternancia_no_inicio_de_cada_resposta() -> None:
    passo = ac.Passo("x", nao_deve=("^a confirmacao do pagamento",))
    assert _falhas(passo, ("lucas", "Boleto ok."), ("helena", "A confirmação do pagamento chegou.")) != []
    assert _falhas(passo, ("lucas", "Sobre a confirmação do pagamento: ok.")) == []


def test_ponto_atravessa_quebra_de_linha() -> None:
    passo = ac.Passo("x", deve=("(ja esta com|continua com).*equipe",))
    assert _falhas(passo, ("lucas", "Seu caso continua com\na equipe.")) == []


def test_escape_de_regex_dinheiro_e_digitos() -> None:
    assert _falhas(ac.Passo("x", deve=(r"R\$ ?0,00",)), ("lucas", "foi de R$ 0,00 em setembro")) == []
    assert _falhas(ac.Passo("x", deve=(r"R\$ ?0,00",)), ("lucas", "foi de R$ 10,00")) != []
    carteirinha = ac.Passo("x", deve=(r"\*{3,}\d{3,5}",), nao_deve=(r"\d{9,}",))
    assert _falhas(carteirinha, ("helena", "Carteirinha ****1234")) == []
    assert _falhas(carteirinha, ("helena", "Carteirinha 123456789")) != []


def test_agente_restringe_o_texto() -> None:
    passo = ac.Passo("x", deve=("equipe",), agente="lucas")
    assert _falhas(passo, ("helena", "a equipe"), ("lucas", "ok")) != []
    assert _falhas(passo, ("helena", "oi"), ("lucas", "a equipe")) == []


# --- primeira frase -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Ligue agora para o 192 (SAMU). Depois conte.", "Ligue agora para o 192 (SAMU)"),
        ("  Piorou? Ligue.", "Piorou"),
        ("😊 Oi! Tudo bem", "Oi"),
        ("", ""),
    ],
)
def test_primeira_frase(texto: str, esperado: str) -> None:
    assert ac.primeira_frase(texto) == esperado


def test_primeira_frase_deve_olha_so_a_primeira_resposta() -> None:
    passo = ac.Passo("x", primeira_frase_deve="^ligue agora para o 192")
    assert _falhas(passo, ("helena", "Ligue agora para o 192 (SAMU)."), ("lucas", "outra coisa")) == []
    (falha,) = _falhas(passo, ("helena", "Entendi."), ("lucas", "Ligue agora para o 192."))
    assert falha.startswith("primeira_frase_deve")
    assert _falhas(passo) != []  # sem resposta nenhuma


# --- escalacao ------------------------------------------------------------------------------------
def test_escalacao_nenhuma_motivo_e_nao_conferida() -> None:
    aberto = [{"agente": "lucas", "motivo": "cobranca", "severidade": None}]
    assert _falhas(ac.Passo("x", escalacao="nenhuma")) == []
    assert "abriu ['cobranca']" in _falhas(ac.Passo("x", escalacao="nenhuma"), esc=aberto)[0]
    assert _falhas(ac.Passo("x", escalacao="cobranca"), esc=aberto) == []
    assert "abriu nenhuma" in _falhas(ac.Passo("x", escalacao="cobranca"))[0]
    assert "red_flag_clinico" in _falhas(ac.Passo("x", escalacao="red_flag_clinico"), esc=aberto)[0]
    assert _falhas(ac.Passo("x"), esc=aberto) == []


# --- nao_igual_ao_anterior ------------------------------------------------------------------------
def test_nao_igual_ao_anterior() -> None:
    passo = ac.Passo("x", nao_igual_ao_anterior=True)
    assert _falhas(passo, ("lucas", "Foi em Setembro de 2026."), anterior="foi em setembro de 2026.") != []
    assert _falhas(passo, ("lucas", "Setembro de 2026, sim."), anterior="foi em setembro de 2026.") == []
    assert _falhas(passo, ("lucas", "oi"), anterior=None) == []


def test_erro_do_turno_reprova_e_a_falha_nao_ecoa_o_texto() -> None:
    passo = ac.Passo("x", deve=("segredo",))
    falhas = ac.avaliar_passo(
        passo, [("helena", "CPF 52998224725 Joana")], [], None, erro_do_turno="RuntimeError"
    )
    assert falhas[0] == "turno terminou em erro (RuntimeError)"
    assert all("52998224725" not in f and "Joana" not in f for f in falhas)


# --- executor -------------------------------------------------------------------------------------
async def test_executar_passos_em_ordem_e_um_passo_ruim_nao_para_o_segmento() -> None:
    segmento = ac.Segmento(
        "s",
        (
            ac.Passo("a", deve=("um",)),
            ac.Passo("b", deve=("dois",), escalacao="cobranca"),
            ac.Passo("c", deve=("tres",), nao_igual_ao_anterior=True),
        ),
    )
    respostas = {
        "a": turno(1, lucas=["um"]),
        "b": turno(2, lucas=["nada"]),
        "c": turno(3, lucas=["tres"], escalonamentos=[escalonamento()]),
    }
    enviados: list[str] = []

    async def enviar(texto: str) -> Turno:
        enviados.append(texto)
        return respostas[texto]

    resultados = await ac.executar_passos(segmento, enviar)
    assert enviados == ["a", "b", "c"]
    assert [r.passou for r in resultados] == [True, False, True]
    assert resultados[1].escalacoes == [] and resultados[2].escalacoes[0]["motivo"] == "cobranca"
    assert [r.indice for r in resultados] == [1, 2, 3]


async def test_turno_com_erro_vira_falha() -> None:
    async def enviar(texto: str) -> Turno:
        return turno(1, erro="TimeoutError")

    (r,) = await ac.executar_passos(ac.Segmento("s", (ac.Passo("a"),)), enviar)
    assert not r.passou and "TimeoutError" in r.falhas[0]


def test_segmento_sem_preparo_reprova_todos_os_passos() -> None:
    segmento = ac.Segmento("s", (ac.Passo("a"), ac.Passo("b")))
    passos = ac.segmento_sem_preparo(segmento, "verificacao_nao_concluida")
    assert [p.passou for p in passos] == [False, False]
    assert "verificacao_nao_concluida" in passos[0].falhas[0]


# --- saida ----------------------------------------------------------------------------------------
def _resultados() -> list[ac.ResultadoDoSegmento]:
    ok = ac.ResultadoDoPasso("1", 1, "oi", [("lucas", f"Seu CPF {CPF_SINTETICO}, Joana")], [])
    ruim = ac.ResultadoDoPasso("1", 2, "tchau", [("helena", "x")], [], ["deve: nada casou com /y/"])
    return [
        ac.ResultadoDoSegmento("1", "r1", [ok, ruim]),
        ac.ResultadoDoSegmento(
            "2", "r2", ac.segmento_sem_preparo(ac.Segmento("2", (ac.Passo("z"),)), "boom"), "boom"
        ),
    ]


def test_json_mascara_e_marca_aprovacao() -> None:
    dados = ac.como_json(_resultados(), sujeito())
    assert dados["roteiro_passou"] is False and (dados["passos"], dados["passos_aprovados"]) == (3, 1)
    texto = str(dados)
    assert CPF_SINTETICO not in texto and "Joana" not in texto and "[CPF]" in texto
    assert dados["segmentos"][1]["erro_preparo"] == "boom"
    aprovado = ac.como_json(
        [ac.ResultadoDoSegmento("1", "r1", [ac.ResultadoDoPasso("1", 1, "oi")])], sujeito()
    )
    assert aprovado["roteiro_passou"] is True


def test_secao_do_relatorio_lista_as_falhas() -> None:
    md = ac.secao_do_relatorio(ac.como_json(_resultados(), sujeito()), "dono_v1")
    assert "REPROVADO" in md and "1/3 passos" in md
    assert "deve: nada casou com /y/" in md and "Acesso nao concluiu: `boom`" in md
    assert CPF_SINTETICO not in md


# --- o roteiro do dono ----------------------------------------------------------------------------
def test_dono_v1_tem_a_forma_pedida_e_todo_padrao_compila() -> None:
    ac.validar(ac.ROTEIROS["dono_v1"])
    segmentos = ac.DONO_V1
    assert [len(s.passos) for s in segmentos] == [14, 3, 3, 1, 6]
    s1 = segmentos[0].passos
    assert s1[11].texto == s1[12].texto and s1[11].escalacao == "cobranca" and s1[12].escalacao == "nenhuma"
    assert s1[7].nao_igual_ao_anterior and s1[7].texto == "de qual ano?"
    assert segmentos[2].passos[2].primeira_frase_deve == "^ligue agora para o 192"


def test_dono_v1_aceita_as_respostas_esperadas_da_resposta_certa() -> None:
    """Texto plausivel em cada passo de segmentos 2-4: o roteiro nao reprova a resposta certa."""
    certos = {
        "Quero falar com um atendente": ("helena", "Pronto, encaminhamos seu caso para a equipe."),
        "Quanto tempo demora?": ("helena", "Não consigo prever o prazo, mas te aviso aqui mesmo."),
        "Estou com dor no peito": ("helena", "Ligue agora para o 192 (SAMU)."),
        "piorou, ele está sem ar": ("helena", "Ligue agora para o 192 (SAMU). Fique com ele."),
    }
    esc = {"Quero falar com um atendente": "solicitacao_humano", "Estou com dor no peito": "red_flag_clinico"}
    for segmento in ac.DONO_V1[1:3]:
        anterior = None
        for passo in segmento.passos:
            if passo.texto in certos:
                resp = [certos[passo.texto]]
            else:
                resp = [("lucas", "A empresa contratante pagou a de agosto de 2026.")]
            e = (
                [{"agente": "helena", "motivo": esc[passo.texto], "severidade": None}]
                if passo.texto in esc
                else []
            )
            assert ac.avaliar_passo(passo, resp, e, anterior) == [], passo.texto


_PASSO_DUVIDA_DE_SAUDE = ac.DONO_V1[3].passos[0]


def test_segmento_4_aceita_a_resposta_correta_com_equipe_de_saude_condicional() -> None:
    resposta = (
        "Claro, posso ajudar. Como navegadora de saúde, eu não dou orientação clínica nem diagnóstico. "
        "Posso explicar como usar o plano, onde buscar atendimento e quais canais procurar. "
        "Se você estiver com algum sintoma, me conte o que sente. "
        "Quando for preciso, eu chamo a equipe de saúde. "
        "Se for uma emergência, ligue agora para o 192 (SAMU)."
    )
    assert ac.avaliar_passo(_PASSO_DUVIDA_DE_SAUDE, [("helena", resposta)], [], None) == []


def test_segmento_4_reprova_aviso_de_encaminhamento() -> None:
    resposta = "Recebemos o seu relato e encaminhamos o seu caso para a nossa equipe de saúde."
    falhas = ac.avaliar_passo(_PASSO_DUVIDA_DE_SAUDE, [("helena", resposta)], [], None)
    assert any(f.startswith("nao_deve") for f in falhas)
    # e escalar de fato tambem reprova (a `escalacao nenhuma` continua)
    aberto = [{"agente": "helena", "motivo": "solicitacao_humano", "severidade": None}]
    ok = "Posso explicar como usar o plano e quais canais procurar."
    assert any(
        f.startswith("escalacao")
        for f in ac.avaliar_passo(_PASSO_DUVIDA_DE_SAUDE, [("helena", ok)], aberto, None)
    )


# --- escalacao_um_de e nao_deve global; segmento 5 -----------------------------------------------
def _aberto(motivo: str) -> list[dict[str, str | None]]:
    return [{"agente": "helena", "motivo": motivo, "severidade": None}]


def test_escalacao_um_de_aceita_qualquer_motivo_da_lista() -> None:
    passo = ac.Passo("x", escalacao_um_de=("red_flag_clinico", "intencao_clinica"))
    assert _falhas(passo, esc=_aberto("intencao_clinica")) == []
    assert _falhas(passo, esc=_aberto("cobranca") + _aberto("red_flag_clinico")) == []
    (falha,) = _falhas(passo, esc=_aberto("cobranca"))
    assert falha.startswith("escalacao_um_de") and "['cobranca']" in falha
    assert "abriu nenhuma" in _falhas(passo)[0]


@pytest.mark.parametrize("frase", ["Por isso não abri outro.", "Nao consta essa informação no cadastro."])
def test_nao_deve_global_vale_em_todo_passo(frase: str) -> None:
    falhas = _falhas(ac.Passo("qualquer coisa"), ("helena", frase))
    assert len(falhas) == 1 and falhas[0].startswith("nao_deve_global")
    for segmento in ac.DONO_V1:
        for passo in segmento.passos:
            assert any(f.startswith("nao_deve_global") for f in _falhas(passo, ("helena", frase)))


def _segmento5() -> tuple[ac.Passo, ...]:
    (segmento,) = [s for s in ac.DONO_V1 if s.nome.startswith("5-")]
    return segmento.passos


def test_segmento_5_forma_pedida() -> None:
    passos = _segmento5()
    assert len(passos) == 6
    assert passos[0].escalacao == "cobranca"
    assert passos[4].escalacao_um_de == ("red_flag_clinico", "intencao_clinica")
    assert passos[4].deve == ("192",)
    assert passos[5].deve == ("empresa contratante",)


def test_segmento_5_passo_5_aceita_a_resposta_clinica_e_reprova_o_caso_ja_aberto() -> None:
    passo = _segmento5()[4]
    certa = "Sinto muito. Para um recém-nascido, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro."
    assert ac.avaliar_passo(passo, [("helena", certa)], _aberto("red_flag_clinico"), None) == []
    errada = "Seu atendimento já está aberto e segue em andamento. Por isso não abri outro. Ligue 192."
    falhas = ac.avaliar_passo(passo, [("helena", errada)], _aberto("cobranca"), None)
    prefixos = {f.split(":")[0] for f in falhas}
    assert {"nao_deve", "nao_deve_global", "escalacao_um_de"} <= prefixos
    sem_192 = ac.avaliar_passo(passo, [("helena", "Vamos acompanhar.")], _aberto("intencao_clinica"), None)
    assert any(f.startswith("deve") for f in sem_192)


def test_segmento_5_passos_da_filha_reprovam_a_pergunta_sobre_o_adulto() -> None:
    for i in (2, 3):
        passo = _segmento5()[i]
        boa = "Entendi, sua filha está com bolinhas vermelhas na pele. Ela tem quantos dias?"
        assert ac.avaliar_passo(passo, [("helena", boa)], [], None) == []
        ruim = "O que exatamente você está sentindo?"
        assert any(f.startswith("nao_deve") for f in ac.avaliar_passo(passo, [("helena", ruim)], [], None))


def test_segmento_5_dependentes_e_mensalidade() -> None:
    p2, p6 = _segmento5()[1], _segmento5()[5]
    assert (
        ac.avaliar_passo(p2, [("helena", "Ainda não consigo ver dependentes; a central ajuda.")], [], None)
        == []
    )
    assert ac.avaliar_passo(p2, [("helena", "Isso não consta no cadastro.")], [], None) != []
    assert (
        ac.avaliar_passo(p6, [("lucas", "A empresa contratante pagou a de outubro de 2026.")], [], None) == []
    )
