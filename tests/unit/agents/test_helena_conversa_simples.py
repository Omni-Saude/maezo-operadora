"""Cobertura assistida — CONVERSA SIMPLES (DL-0131, decisao do dono de 20/10/2026 "avance").

Titular por padrao, a pessoa lembrada, a intencao sem nome pede a lista, a lista inteira respondida (ate' 15,
aviso acima), os nao localizados numa linha com a equipe UMA vez, resposta curta presa a pendencia e pergunta
nova escapando, irritacao sem caso, nomes em Title Case e o parentesco no lugar de "dependente N", e a batida
na cabeca com perguntas de alerta proprias (DL-0132). Casos GENERICOS (nenhum texto do teste do dono)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_fluxo import (
    PEND_RESPONDIDO,
    Dependencias,
    FluxoDeCobertura,
    cita_outra_pessoa,
    quem_pelo_texto,
)
from maezo.agents.helena.consultas_plano import NomesDoTurno, texto_do_parentesco
from maezo.conhecimento.catalogo_procedimentos import BuscaDeProcedimentos, carregar_sinonimos
from tests.unit.agents.test_helena_cobertura_assistida import _CATALOGO, _Item, _Mundo, _pedido, _View


def _fluxo(m: _Mundo, *, lembrado: list[str] | None = None, lote: Any = None) -> FluxoDeCobertura:
    busca = BuscaDeProcedimentos(transporte=None, sinonimos=carregar_sinonimos())
    busca.carregar(_CATALOGO, "v1")
    memoria = lembrado if lembrado is not None else []

    def rotulo(quem: str) -> str:
        return ca.PARA_O_TITULAR if quem == "titular" else "Para Ana"

    return FluxoDeCobertura(
        Dependencias(
            extrair=m.extrair,
            cobertura=m.cobertura,
            dependentes=m.dependentes,
            elegibilidade=m.eleg,
            busca=busca,
            pergunta_para_quem=lambda graus, com_nome: "Para quem é?",
            texto_geral=m.geral,
            escolher_varios=lote,
            quem_lembrado=lambda: memoria[-1] if memoria else None,
            lembrar_quem=memoria.append,
            rotulo_de_quem=rotulo,
        )
    )


_HEMO = {"40304361": _View((_Item("HEMOGRAMA"),))}


# --- titular por padrao e a pessoa lembrada -----------------------------------------------------------------
async def test_sem_citar_ninguem_responde_para_o_titular_e_convida_o_dependente() -> None:
    m = _Mundo(pedido=_pedido("hemograma"), graus=["filho ou filha"], coberturas=_HEMO)
    up = await _fluxo(m).responder("meu plano cobre hemograma?", "", "")
    texto = up["response_text"]
    assert texto.startswith(f"{ca.PARA_O_TITULAR}, pelo cadastro") and ca.SUFIXO_DO_DEPENDENTE in texto
    assert m.consultas == ["40304361"] and "Para quem" not in texto


async def test_a_pessoa_escolhida_e_lembrada_na_pergunta_seguinte() -> None:
    m = _Mundo(pedido=_pedido("hemograma"), coberturas=_HEMO)
    memoria = ["dependente:1"]
    up = await _fluxo(m, lembrado=memoria).responder("cobre hemograma?", "", "")
    assert up["response_text"].startswith("Para Ana, pelo cadastro")
    assert ca.SUFIXO_DO_DEPENDENTE not in up["response_text"]


async def test_citar_o_filho_com_dois_filhos_pergunta_qual() -> None:
    m = _Mundo(pedido=_pedido("hemograma", quem_texto="filho"), graus=["filho ou filha", "filho ou filha"])
    up = await _fluxo(m).responder("cobre hemograma para o meu filho?", "", "")
    assert up["coleta_pendente"] == ca.PEND_QUEM and m.consultas == []


def test_titular_dito_no_meio_da_frase() -> None:
    assert quem_pelo_texto("eu ja tinha dito que e pra mim, titular!!!", ["filho"], {}) == "titular"
    assert cita_outra_pessoa("cobre hemograma pro meu filho?") and not cita_outra_pessoa("cobre hemograma?")


# --- intencao sem nome e a lista ----------------------------------------------------------------------------
async def test_intencao_sem_o_nome_pede_a_lista_e_a_lista_e_respondida_inteira() -> None:
    m = _Mundo(
        pedido=_pedido(),
        coberturas={
            "40304361": _View((_Item("HEMOGRAMA"),)),
            "40808041": _View((_Item("TC TORAX"),)),
            "40101010": _View((_Item("ECG"),)),
        },
    )
    fluxo = _fluxo(m)
    up = await fluxo.responder("preciso saber se meu plano cobre alguns exames", "", "")
    assert up["response_text"] == ca.TEXTO_PEDE_LISTA and up["coleta_pendente"] == ca.PEND_FORMA
    m.pedido = _pedido("hemograma", "tomografia de torax", "eletrocardiograma", "exame zzz inexistente")
    m.extracoes = 0  # a proxima extracao devolve o pedido do teste
    up2 = await fluxo.responder(
        "HEMOGRAMA\ntomografia de torax\neletrocardiograma\nexame zzz inexistente",
        up["coleta_pendente"],
        up["coleta_contexto"],
    )
    texto = up2["response_text"]
    assert "1) " in texto and "3) " in texto, "todos os achados respondidos"
    assert texto.count("Não localizei no cadastro") == 1 and "exame zzz inexistente" in texto
    assert up2["coleta_pendente"] == ca.PEND_EQUIPE, "a equipe e' oferecida UMA vez, no fim"


async def test_acima_de_15_responde_os_15_primeiros_e_avisa() -> None:
    nomes = [f"exame {i}" for i in range(17)]
    bruto = json.dumps({"procedimentos": nomes, "tipo": "procedimento", "assunto": "cobertura"})
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is not None and len(pedido.procedimentos) == ca.MAX_PROCEDIMENTOS and pedido.total == 17
    m = _Mundo(pedido=pedido)
    up = await _fluxo(m).responder("cobre " + ", ".join(nomes) + "?", "", "")
    assert ca.aviso_do_limite(17) in up["response_text"]


async def test_a_lista_vai_ao_modelo_numa_chamada_so() -> None:
    chamadas: list[int] = []

    async def lote(pedidos: Sequence[tuple[str, Sequence[str]]]) -> list[ca.Escolha | None]:
        chamadas.append(len(pedidos))
        return [None] * len(pedidos)

    m = _Mundo(pedido=_pedido("hemograma", "tomografia de torax", para_quem="titular"), coberturas=_HEMO)
    await _fluxo(m, lote=lote).responder("cobre hemograma e tomografia de torax?", "", "")
    assert chamadas == [2]


def test_itens_da_lista_de_qualquer_jeito() -> None:
    texto = "HEMOGRAMA\n- glicose\n1) ultrassom de abdome; raio x de torax e eletrocardiograma"
    assert ca.itens_da_lista(texto) == [
        "HEMOGRAMA",
        "glicose",
        "ultrassom de abdome",
        "raio x de torax",
        "eletrocardiograma",
    ]


# --- pendencia: so' a resposta curta; irritacao ------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "curta"),
    [
        ("1", True),
        ("sim", True),
        ("ok.", True),
        ("Ana", True),
        ("eu ja disse que e pra mim", True),
        ("Quem são meus dependentes?", False),
        ("quero saber da minha carência", False),
        ("qual o telefone da central?", False),
    ],
)
def test_resposta_curta(texto: str, curta: bool) -> None:
    assert ca.resposta_curta(texto) is curta


def test_quero_alguma_coisa_nao_e_sim() -> None:
    assert ca.ler_resposta("quero saber da minha carencia") is None
    assert ca.ler_resposta("quero sim, por favor") == "sim"


async def test_irritacao_com_pedido_guardado_pede_desculpa_e_tenta_de_novo() -> None:
    m = _Mundo(pedido=_pedido("hemograma"), coberturas=_HEMO)
    ctx = ca.Contexto(termos=["hemograma"], quem="titular", respondidos=["Hemograma"])
    up = await _fluxo(m).responder(
        "Ué, como assim? você acabou de verificar", PEND_RESPONDIDO, ctx.codificar()
    )
    assert up["response_text"].startswith(ca.TEXTO_DESCULPA) and m.consultas == ["40304361"]


async def test_eu_ja_disse_responde_o_para_quem_com_desculpa() -> None:
    m = _Mundo(pedido=_pedido("hemograma"), graus=["filho ou filha"], coberturas=_HEMO)
    ctx = ca.Contexto(termos=["hemograma"])
    up = await _fluxo(m).responder("EU JÁ TINHA DITO QUE É PRA MIM TITULAR!!!", ca.PEND_QUEM, ctx.codificar())
    assert up["response_text"].startswith(ca.TEXTO_DESCULPA) and m.consultas == ["40304361"]


# --- nomes ----------------------------------------------------------------------------------------------
def test_nome_em_title_case_e_parentesco_no_lugar_de_dependente_n() -> None:
    assert NomesDoTurno({1: "ANA MARIA DE SOUZA"}).preencher("{{DEPENDENTE:1}}") == "Ana Maria de Souza"
    assert NomesDoTurno({1: "João d'Ávila"}).preencher("{{DEPENDENTE:1}}") == "João d'Ávila"
    assert texto_do_parentesco("Filho(a) até 23 anos") == "o seu filho"
    assert texto_do_parentesco("Cônjuge") == "o seu cônjuge"
    assert ca.nome_proprio("CARLOS DA SILVA") == "Carlos da Silva"


def test_contexto_grande_nunca_corta_o_json() -> None:
    ctx = ca.Contexto(termos=[f"exame {i}" * 20 for i in range(15)], respondidos=["x" * 3000] * 4)
    volta = ca.Contexto.decodificar(ctx.codificar())
    assert volta.termos == ctx.termos[: ca.MAX_PROCEDIMENTOS]


# --- grafo: lista sem "cobre", pendencia que escapa, irritacao sem caso -------------------------------------
async def test_grafo_lista_sem_cobre_vai_a_cobertura() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    grafo, whatsapp, _, fonte = _grafo_e_estado(
        {"40304361": _View((_Item("HEMOGRAMA"),)), "40808041": _View((_Item("TC TORAX"),))},
        [_json_pedido("hemograma", "tomografia de torax")],
    )
    await grafo.ainvoke(_entrada("Hemograma\nTomografia de torax"))
    assert sorted(fonte.pedidos) == ["40304361", "40808041"]
    assert "procedimento específico" not in whatsapp.enviados[-1]


async def test_grafo_pergunta_nova_escapa_da_pendencia() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    grafo, whatsapp, _, fonte = _grafo_e_estado({}, [_json_pedido("exame zzz inexistente")])
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre o exame zzz inexistente?"))
    assert r1["coleta_pendente"] == ca.PEND_FORMA
    antes = len(fonte.pedidos)
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    await grafo.ainvoke(_entrada("Quem são meus dependentes?") | memoria)
    assert len(fonte.pedidos) == antes, "a pergunta nova nao virou busca de procedimento"
    assert "Não localizei" not in whatsapp.enviados[-1]


async def test_grafo_irritacao_nao_abre_caso() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

    grafo, _, cib, _ = _grafo_e_estado(
        {"40304361": _View((_Item("HEMOGRAMA"),))}, [_json_pedido("hemograma")]
    )
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre hemograma?"))
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    await grafo.ainvoke(
        _entrada("Ué como assim? você acabou de verificar e falou outra coisa com a equipe") | memoria
    )
    assert _starts(cib) == 0


def test_batida_na_cabeca_tem_perguntas_proprias() -> None:
    from pathlib import Path

    from maezo.conhecimento import base_saude as bs

    raiz = Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "saude"
    base = bs.carregar_base(raiz)
    busca = bs.buscar(base, "Meu filho caiu no chão e bateu a cabeça", categorias=(bs.CATEGORIA_SINTOMA,))
    assert busca.achado == "sin-batida-na-cabeca"
    assunto = base.get("sin-batida-na-cabeca")
    assert assunto is not None and assunto.populacao == "todos"
    assert any("vômito" in p for p in assunto.perguntas_alerta or ())
    assert bs.alarme_no_texto(base, "minha filha bateu a cabeça e vomitou")


# --- Avaliacao de 21/10/2026: fala depois da resposta, lista mista, discordancia, lote ----------------------
async def test_cortesia_depois_da_resposta_e_curta_e_nao_busca() -> None:
    m = _Mundo(pedido=_pedido("hemograma"), coberturas=_HEMO)
    ctx = ca.Contexto(termos=["hemograma"], quem="titular", respondidos=["Hemograma"])
    for fala, esperado in (
        ("valeu!", ca.TEXTO_DE_NADA),
        ("ok", ca.TEXTO_COMBINADO),
        ("blz", ca.TEXTO_COMBINADO),
    ):
        up = await _fluxo(m).responder(fala, PEND_RESPONDIDO, ctx.codificar())
        assert up["response_text"] == esperado
    assert m.consultas == [] and m.extracoes == 0


async def test_faltou_algum_confere_a_lista_anterior() -> None:
    m = _Mundo()
    ctx = ca.Contexto(termos=["hemograma", "glicose", "tsh"], quem="titular")
    up = await _fluxo(m).responder("faltou algum?", PEND_RESPONDIDO, ctx.codificar())
    assert up["response_text"] == ca.texto_do_que_respondi(["hemograma", "glicose", "tsh"])
    assert m.consultas == []


async def test_para_quem_dito_depois_refaz_a_consulta_para_a_pessoa() -> None:
    m = _Mundo(graus=["cônjuge"], coberturas=_HEMO)
    ctx = ca.Contexto(termos=["hemograma"], quem="titular", respondidos=["HEMOGRAMA"], padrao=True)
    up = await _fluxo(m).responder("é pra minha esposa", PEND_RESPONDIDO, ctx.codificar())
    assert up["response_text"].startswith("Para Ana") and m.consultas == ["40304361"]
    assert "Já te passei" not in up["response_text"], "outra pessoa nao e' a mesma pergunta"


async def test_lista_mista_responde_cada_item_pelo_seu_caminho() -> None:
    pedido = ca.Pedido(
        procedimentos=("hemograma",),
        outros=(("dipirona", "remedio"), ("botox", "estetica")),
        pelo_modelo=True,
        total=1,
    )
    m = _Mundo(pedido=pedido, coberturas=_HEMO)
    up = await _fluxo(m).responder("cobre hemograma, dipirona e botox?", "", "")
    texto = up["response_text"]
    assert "dipirona:" in texto and "botox:" in texto and "consta no Rol" in texto
    assert up["coleta_pendente"] == ca.PEND_EQUIPE


def test_o_modelo_devolve_os_outros_itens() -> None:
    bruto = json.dumps(
        {
            "procedimentos": ["hemograma"],
            "outros_itens": [{"nome": "dipirona", "tipo": "remedio"}, {"nome": "x"}],
        }
    )
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is not None and pedido.outros == (("dipirona", "remedio"),)


async def test_discordancia_sem_procedimento_nao_termina_na_lista() -> None:
    m = _Mundo(pedido=ca.Pedido(discorda=True, pelo_modelo=True))
    up = await _fluxo(m).responder("o médico disse que cobre e vocês negaram", "", "")
    assert ca.TEXTO_PEDE_LISTA not in up["response_text"] and up["coleta_pendente"] == ca.PEND_EQUIPE


async def test_item_que_o_lote_nao_achou_ganha_a_busca_de_item_unico() -> None:
    from maezo.agents.helena.cobertura_fluxo import DIRETO

    async def lote(pedidos: Sequence[tuple[str, Sequence[str]]]) -> list[ca.Escolha | None]:
        return [ca.Escolha(None, "baixa") for _ in pedidos]

    m = _Mundo(pedido=_pedido("hemograma", "tomografia de torax"), coberturas=_HEMO)
    fluxo = _fluxo(m, lote=lote)
    chamadas: list[str] = []

    async def escolher(termos: Sequence[str], termo: str, nomes: Sequence[str]) -> ca.Escolha:
        chamadas.append(termo)
        por_nome = {p.nome: p.codigo for p in _CATALOGO}
        alvo = "40304361" if "hemograma" in termo else "40808041"
        return ca.Escolha(next(i for i, n in enumerate(nomes) if por_nome.get(n) == alvo), "alta")

    fluxo.d.escolher = escolher
    decisoes = await fluxo._decidir_varios(
        ca.Contexto(termos=["hemograma", "tomografia de torax"]), ["hemograma", "tomografia de torax"]
    )
    assert [d.tipo for d in decisoes if d is not None] == [DIRETO, DIRETO] and len(chamadas) == 2


async def test_grafo_depois_da_resposta_pergunta_de_outro_assunto_escapa() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _starts

    grafo, whatsapp, cib, fonte = _grafo_e_estado(
        {"40304361": _View((_Item("HEMOGRAMA"),))}, [_json_pedido("hemograma"), "", _classify()]
    )
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre hemograma?"))
    assert r1["coleta_pendente"] == PEND_RESPONDIDO
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    antes = len(fonte.pedidos)
    await grafo.ainvoke(_entrada("quero falar com um atendente") | memoria)
    assert _starts(cib) == 1, "o pedido explicito de pessoa abre o caso"
    assert ca.TEXTO_PEDE_LISTA not in whatsapp.enviados[-1] and len(fonte.pedidos) == antes


# --- "tem varios tipos" so' para categoria real (avaliacao de 21/10/2026) -----
async def _decisao_vaga(termo: str) -> Any:
    from tests.unit.agents.test_helena_cobertura_assistida import _busca

    m = _Mundo()
    fluxo = _fluxo(m)
    busca = _busca()
    candidatos = await fluxo._recuperar(busca, termo, "")
    return await fluxo._da_escolha(busca, termo, "", candidatos, ca.Escolha(None, "baixa", vago=True))


async def test_termo_inventado_vago_vira_nao_localizado() -> None:
    from maezo.agents.helena.cobertura_fluxo import NENHUM

    assert (await _decisao_vaga("qwxzk")).tipo == NENHUM


async def test_categoria_real_vaga_continua_perguntando_qual() -> None:
    from maezo.agents.helena.cobertura_fluxo import VAGO

    assert (await _decisao_vaga("ultrassonografia")).tipo == VAGO


# --- roteiro de 22/10/2026 (roteador ativo) -----
async def test_discordancia_sem_procedimento_vence_o_desvio_da_autorizacao() -> None:
    m = _Mundo(pedido=ca.Pedido(discorda=True, assunto="autorizacao", pelo_modelo=True))
    up = await _fluxo(m).responder("a clinica disse que cobre, mas voces recusaram a minha guia", "", "")
    assert "desviar_para" not in up
    assert up["response_text"].endswith(ca.TEXTO_OFERTA_DA_DISCORDANCIA)
    assert up["coleta_pendente"] == ca.PEND_EQUIPE


async def test_lista_mista_com_exclusao_responde_todos_os_itens() -> None:
    pedido = ca.Pedido(
        procedimentos=("hemograma",),
        outros=(("dipirona", "remedio"),),
        exclusao_legal="estetica",
        pelo_modelo=True,
        total=1,
    )
    m = _Mundo(pedido=pedido, coberturas=_HEMO)
    up = await _fluxo(m).responder("cobre hemograma, dipirona e preenchimento labial?", "", "")
    texto = up["response_text"]
    assert "dipirona:" in texto and ca.NOME_DO_ITEM_EXCLUIDO["estetica"] in texto and "consta no Rol" in texto
