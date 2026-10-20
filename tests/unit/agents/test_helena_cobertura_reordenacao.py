"""Cobertura assistida, reordenacao e estado da conversa (DL-0129; avaliacao independente de 20/10/2026).

Universo so' assistencial (regra de CATALOGO), recuperacao + escolha do modelo (alta/media/baixa/vago),
confirmacao de uma linha, resposta lida CONTRA a pendencia, varios procedimentos, D2-D4, erro de digitacao.
Casos GENERICOS: nenhum termo do conjunto do avaliador."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_fluxo import PEND_RESPONDIDO, Dependencias, FluxoDeCobertura
from maezo.conhecimento.catalogo_procedimentos import (
    BuscaDeProcedimentos,
    Procedimento,
    administrativo,
    assistencial,
    carregar_sinonimos,
    corrigir_palavra,
)
from tests.unit.agents.test_helena_cobertura_assistida import _CATALOGO, _Item, _Mundo, _pedido, _View


# --- universo assistencial ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("descricao", "grupo", "codigo", "esperado"),
    [
        ("ALUGUEL TAXA DE APARELHO DE RAIO X POR USO", "ALUGUEL", "60024674", True),
        ("REVISAO DA TOMOGRAFIA COMPUTADORIZADA POR PROCEDIMENTO", "PROCEDIMENTOS", "41003012", True),
        ("LAUDO EVOLUTIVO DE RAIO X POR PROCEDIMENTO", "OUTROS EXAMES", "40000001", True),
        ("PCT CIRURGIA AMBULATORIAL", "OUTROS", "98000001", True),
        ("DIARIA DE APARTAMENTO", "INDIVIDUAL", "60000001", True),
        ("COPARTICIPACAO", "ITEM DE SAIDA", "15010202", True),
        ("TAXA DE SALA CIRURGICA", "TAXAS DE SALA", "10907104", True),
        ("TC TORAX", "TOMOGRAFIA", "41001230", False),
        ("REVISAO CIRURGICA DE PROTESE DE QUADRIL", "QUADRIL", "30700001", False),
        ("CONSULTA EM CONSULTORIO", "CONSULTAS", "10101012", False),
        ("RESTAURACAO EM RESINA", "ODONTOLOGIA RESTAURADORA", "85100001", False),
        ("REMOCAO DE CORPO ESTRANHO DA CORNEA", "OLHOS", "30300001", False),
    ],
)
def test_administrativo_por_regra_de_catalogo(
    descricao: str, grupo: str, codigo: str, esperado: bool
) -> None:
    assert administrativo(descricao, grupo, codigo) is esperado


def test_o_indice_so_tem_itens_assistenciais() -> None:
    busca = BuscaDeProcedimentos(transporte=None)
    busca.carregar(
        [
            *_CATALOGO,
            Procedimento("60024674", "ALUGUEL TAXA DE APARELHO", None, "ALUGUEL"),
            Procedimento("41003012", "REVISAO DA TOMOGRAFIA POR PROCEDIMENTO", None, "PROCEDIMENTOS"),
            Procedimento("98000001", "PCT CIRURGIA", None, "OUTROS"),
        ],
        "v",
    )
    assert busca._fora_do_universo == 3
    assert all(assistencial(p) for p in busca._itens)


@pytest.mark.parametrize(
    ("palavra", "esperada"),
    [
        ("hemogama", "hemograma"),
        ("tomogafia", "tomografia"),
        ("torax", None),
        ("abc", None),
        ("zzzzzz", None),
    ],
)
def test_erro_de_digitacao_vira_a_palavra_do_catalogo(palavra: str, esperada: str | None) -> None:
    vocabulario = {"hemograma": 9, "tomografia": 7, "torax": 5, "abdome": 4}
    assert corrigir_palavra(palavra, vocabulario) == esperada


async def test_a_busca_corrige_a_digitacao_antes_de_buscar() -> None:
    busca = BuscaDeProcedimentos(transporte=None, sinonimos=carregar_sinonimos())
    busca.carregar(_CATALOGO, "v1")
    resultado = await busca.buscar("hemogama")
    assert resultado.candidatos and resultado.candidatos[0].procedimento.codigo == "40304361"


# --- escolha do modelo -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("bruto", "indice", "confianca", "vago"),
    [
        ('{"escolha": 2, "confianca": "alta", "alternativas": [1], "nome_leigo": "x"}', 1, "alta", False),
        ('{"escolha": 9, "confianca": "alta"}', None, "baixa", False),
        ('{"escolha": 1, "confianca": "certeza"}', 0, "baixa", False),
        ('{"escolha": 1, "confianca": "alta", "vago": true, "alternativas": [2]}', None, "baixa", True),
    ],
)
def test_a_escolha_do_modelo_so_vale_dentro_da_lista(
    bruto: str, indice: int | None, confianca: str, vago: bool
) -> None:
    e = ca.escolha_do_modelo(bruto, 3)
    assert e is not None and (e.indice, e.confianca, e.vago) == (indice, confianca, vago)
    assert ca.escolha_do_modelo("sem json", 3) is None


class _Escolhedor:
    def __init__(self, resposta: ca.Escolha | None | Exception) -> None:
        self.resposta = resposta
        self.chamadas: list[tuple[str, int]] = []

    async def __call__(self, termos: Sequence[str], termo: str, nomes: Sequence[str]) -> ca.Escolha | None:
        self.chamadas.append((termo, len(nomes)))
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


def _fluxo(m: _Mundo, escolher: Any) -> FluxoDeCobertura:
    busca = BuscaDeProcedimentos(transporte=None, sinonimos=carregar_sinonimos())
    busca.carregar(_CATALOGO, "v1")
    return FluxoDeCobertura(
        Dependencias(
            extrair=m.extrair,
            cobertura=m.cobertura,
            dependentes=m.dependentes,
            elegibilidade=m.eleg,
            busca=busca,
            pergunta_para_quem=lambda graus, com_nome: "Para quem é?",
            texto_geral=m.geral,
            escolher=escolher,
        )
    )


class _EscolhePorCodigo:
    """Escolhe o candidato de um CODIGO com a confianca dada (como o modelo faria pelo numero)."""

    def __init__(self, busca_codigo: str, confianca: str, alternativas: Sequence[str] = ()) -> None:
        self.codigo, self.confianca, self.alternativas = busca_codigo, confianca, alternativas
        self.nomes_vistos: list[str] = []

    async def __call__(self, termos: Sequence[str], termo: str, nomes: Sequence[str]) -> ca.Escolha | None:
        self.nomes_vistos = list(nomes)
        por_nome = {p.nome: p.codigo for p in _CATALOGO}
        posicoes = [i for i, n in enumerate(nomes) if por_nome.get(n) == self.codigo]
        alts = tuple(i for i, n in enumerate(nomes) if por_nome.get(n) in self.alternativas)
        return ca.Escolha(posicoes[0] if posicoes else None, self.confianca, alts, "nome simples")


async def test_alta_confianca_consulta_direto_pelo_codigo() -> None:
    m = _Mundo(
        pedido=_pedido("ultrassom do abdome", para_quem="titular"),
        coberturas={"40901122": _View((_Item("US ABDOME TOTAL"),))},
    )
    up = await _fluxo(m, _EscolhePorCodigo("40901122", "alta")).responder(
        "cobre ultrassom do abdome?", "", ""
    )
    assert m.consultas == ["40901122"] and up["coleta_pendente"] == PEND_RESPONDIDO


async def test_media_confirma_em_uma_linha_e_o_sim_consulta() -> None:
    m = _Mundo(
        pedido=_pedido("ultrassom do abdome", para_quem="titular"),
        coberturas={"40901122": _View((_Item("US ABDOME TOTAL"),))},
    )
    fluxo = _fluxo(m, _EscolhePorCodigo("40901122", "media", ("40901114",)))
    up = await fluxo.responder("cobre ultrassom do abdome?", "", "")
    assert up["coleta_pendente"] == ca.PEND_CONFIRMA and m.consultas == []
    assert up["response_text"] == ca.TEXTO_CONFIRMA.format(nome="nome simples")
    up2 = await fluxo.responder("quero sim, por favor", up["coleta_pendente"], up["coleta_contexto"])
    assert m.consultas == ["40901122"] and "consta no Rol" in up2["response_text"]


async def test_media_e_o_nao_mostra_as_alternativas() -> None:
    m = _Mundo(pedido=_pedido("ultrassom do abdome", para_quem="titular"))
    fluxo = _fluxo(m, _EscolhePorCodigo("40901122", "media", ("40901114",)))
    up = await fluxo.responder("cobre ultrassom do abdome?", "", "")
    up2 = await fluxo.responder("não", up["coleta_pendente"], up["coleta_contexto"])
    assert up2["coleta_pendente"] == ca.PEND_FORMA or up2["coleta_pendente"] == ca.PEND_OPCOES
    assert m.consultas == []


async def test_baixa_com_alternativas_vira_opcoes_sem_consultar() -> None:
    m = _Mundo(pedido=_pedido("ultrassom do abdome", para_quem="titular"))
    fluxo = _fluxo(m, _EscolhePorCodigo("40901122", "baixa", ("40901114",)))
    up = await fluxo.responder("cobre ultrassom do abdome?", "", "")
    assert up["coleta_pendente"] == ca.PEND_OPCOES and m.consultas == []
    await fluxo.responder("o segundo", up["coleta_pendente"], up["coleta_contexto"])
    assert len(m.consultas) == 1


async def test_pedido_so_da_categoria_pergunta_qual() -> None:
    m = _Mundo(pedido=_pedido("ultrassom", para_quem="titular"))
    escolher = _Escolhedor(ca.Escolha(None, "baixa", (), "", vago=True))
    up = await _fluxo(m, escolher).responder("cobre ultrassom?", "", "")
    assert up["response_text"] == ca.pergunta_do_tipo("ultrassom") and up["coleta_pendente"] == ca.PEND_FORMA
    assert "Não localizei" not in up["response_text"] and m.consultas == []


async def test_falha_do_modelo_cai_na_decisao_por_nota() -> None:
    m = _Mundo(
        pedido=_pedido("tomografia de torax", para_quem="titular"),
        coberturas={"40808041": _View((_Item("TC TORAX"),))},
    )
    up = await _fluxo(m, _Escolhedor(TimeoutError())).responder("cobre tomografia de torax?", "", "")
    assert m.consultas == ["40808041"] and up["coleta_pendente"] == PEND_RESPONDIDO


# --- estado da conversa: a resposta e' lida contra a pendencia --------------------------------------------
@pytest.mark.parametrize(
    ("texto", "leitura"),
    [
        ("sim", "sim"),
        ("Quero sim, por favor!", "sim"),
        ("pode passar", "sim"),
        ("isso mesmo", "sim"),
        ("não, obrigada", "nao"),
        ("não sei o nome direito", "nao_sei"),
        ("não lembro como está no pedido", "nao_sei"),
        ("deixa pra lá", "encerrar"),
        ("esquece", "encerrar"),
        ("ok obrigado", None),
        ("tomografia do abdome", None),
    ],
)
def test_ler_resposta(texto: str, leitura: str | None) -> None:
    assert ca.ler_resposta(texto) == leitura


@pytest.mark.parametrize(
    "pendente", [ca.PEND_FORMA, ca.PEND_OPCOES, ca.PEND_CONFIRMA, ca.PEND_QUEM, ca.PEND_EQUIPE]
)
async def test_deixa_pra_la_encerra_em_qualquer_pendencia(pendente: str) -> None:
    m = _Mundo(pedido=_pedido("hemograma", para_quem="titular"))
    ctx = ca.Contexto(termos=["hemograma"], opcoes=[("40304361", "HEMOGRAMA")])
    up = await _fluxo(m, None).responder("deixa pra lá", pendente, ctx.codificar())
    assert up["response_text"] == ca.TEXTO_ASSUNTO_ENCERRADO and up["coleta_pendente"] is None
    assert m.consultas == [] and m.extracoes == 0, "nunca vira busca"


async def test_nao_sei_o_nome_oferece_a_equipe_sem_buscar() -> None:
    m = _Mundo(pedido=_pedido("exame raro", para_quem="titular"))
    ctx = ca.Contexto(termos=["exame raro"], tentativas=1, quem="titular")
    up = await _fluxo(m, None).responder("não sei o nome direito", ca.PEND_FORMA, ctx.codificar())
    assert up["response_text"] == ca.TEXTO_OFERTA_EQUIPE and up["coleta_pendente"] == ca.PEND_EQUIPE
    assert m.consultas == [] and m.extracoes == 0
    resumo = ca.resumo_para_a_equipe(ca.Contexto.decodificar(up["coleta_contexto"]))
    assert "exame raro" in resumo


async def test_sim_ambiguo_com_varias_opcoes_repete_a_pergunta() -> None:
    m = _Mundo()
    ctx = ca.Contexto(termos=["x"], opcoes=[("1", "A"), ("2", "B")])
    up = await _fluxo(m, None).responder("sim", ca.PEND_OPCOES, ctx.codificar())
    assert up["coleta_pendente"] == ca.PEND_OPCOES and m.consultas == []


# --- varios procedimentos: cada um traduzido e consultado -------------------------------------------------
async def test_varios_procedimentos_cada_um_pela_escolha() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma", "tomografia de torax", para_quem="titular"),
        coberturas={"40304361": _View((_Item("HEMOGRAMA"),)), "40808041": _View((_Item("TC TORAX"),))},
    )

    async def escolher(termos: Sequence[str], termo: str, nomes: Sequence[str]) -> ca.Escolha:
        alvo = "40304361" if "hemograma" in termo else "40808041"
        por_nome = {p.nome: p.codigo for p in _CATALOGO}
        return ca.Escolha(next(i for i, n in enumerate(nomes) if por_nome.get(n) == alvo), "alta")

    up = await _fluxo(m, escolher).responder("cobre hemograma e tomografia de torax?", "", "")
    assert sorted(m.consultas) == ["40304361", "40808041"]
    assert "1) " in up["response_text"] and "2) " in up["response_text"]


# --- D2-D4 e remedio x exame -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "assunto"),
    [
        ("o plano reembolsa a consulta particular?", "reembolso"),
        ("quanto pago de coparticipação na tomografia?", "coparticipacao"),
        ("cobre tomografia no hospital central?", "rede"),
        ("cobre tomografia?", "cobertura"),
    ],
)
def test_o_texto_confirma_o_assunto(texto: str, assunto: str) -> None:
    pedido = ca.confirmar_pelo_texto(ca.Pedido(procedimentos=("tomografia",), pelo_modelo=True), texto)
    assert pedido.assunto == assunto


def test_dosagem_da_substancia_e_exame_nao_remedio() -> None:
    base = ca.Pedido(procedimentos=("insulina",), tipo="remedio", pelo_modelo=True)
    assert ca.confirmar_pelo_texto(base, "cobre insulina em jejum?").tipo == "procedimento"
    assert ca.confirmar_pelo_texto(base, "o plano cobre a insulina que eu uso?").tipo == "remedio"


async def test_cobre_no_hospital_responde_a_cobertura_e_diz_onde_ver_a_rede() -> None:
    m = _Mundo(
        pedido=_pedido("tomografia de torax", para_quem="titular", assunto="rede"),
        coberturas={"40808041": _View((_Item("TC TORAX"),))},
    )
    up = await _fluxo(m, _EscolhePorCodigo("40808041", "alta")).responder(
        "cobre tomografia de torax no hospital central?", "", ""
    )
    assert m.consultas == ["40808041"] and ca.TEXTO_REDE_DO_PROCEDIMENTO in up["response_text"]


def test_coparticipacao_e_reembolso_do_procedimento_ficam_com_a_helena() -> None:
    from maezo.agents.helena.graph import _copart_ou_reembolso_do_procedimento as copart

    assert copart("quanto pago de coparticipacao na tomografia")
    assert copart("o plano reembolsa consulta particular que eu paguei")
    assert not copart("veio coparticipacao no meu boleto desse mes")
    assert not copart("qual o valor da minha mensalidade")


def test_textos_novos_passam_nas_cercas() -> None:
    from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa

    for t in (ca.TEXTO_ASSUNTO_ENCERRADO, ca.TEXTO_CONFIRMA.format(nome="x"), ca.TEXTO_REDE_DO_PROCEDIMENTO,
              ca.pergunta_do_tipo("ultrassom")):  # fmt: skip
        assert motivo_de_recusa(t, "inform", start_aconteceu=False) is None, t
        assert motivo_de_canal_nao_confirmado(t) is None, t


def test_contexto_guarda_o_nome_tecnico() -> None:
    ctx = ca.Contexto(termos=["a", "b"], tecnicos=["A1", ""])
    volta = ca.Contexto.decodificar(ctx.codificar())
    assert volta.tecnico_de("a") == "A1" and volta.tecnico_de("b") == "" and volta.tecnico_de("z") == ""


def test_o_modelo_devolve_o_nome_tecnico_alinhado() -> None:
    bruto = json.dumps({"procedimentos": ["operacao x", "exame y"], "nomes_tecnicos": ["tecnico x"]})
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is not None and pedido.tecnicos == ("tecnico x", "")


async def test_escolha_alta_sem_palavra_em_comum_vira_confirmacao() -> None:
    """A conferencia lexica: "alta" num item que nao divide palavra com o pedido nunca consulta direto."""
    from maezo.conhecimento.catalogo_procedimentos import Candidato

    m = _Mundo(pedido=_pedido("ultrassom do abdome", para_quem="titular"))
    fluxo = _fluxo(m, _EscolhePorCodigo("40304361", "alta"))
    busca = fluxo.d.busca
    assert busca is not None
    hemograma = next(p for p in _CATALOGO if p.codigo == "40304361")

    async def recuperar(termo: str, n: int = 20) -> tuple[Candidato, ...]:
        return (Candidato(hemograma, 0.9, 0.9, 0.0),)

    busca.recuperar = recuperar  # type: ignore[method-assign]
    up = await fluxo.responder("cobre ultrassom do abdome?", "", "")
    assert up["coleta_pendente"] == ca.PEND_CONFIRMA and m.consultas == []


# --- grafo: a resposta contra a pendencia, ponta a ponta ----------------------------------------------------
async def test_grafo_nao_sei_o_nome_e_o_quero_sim_abrem_o_caso() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

    grafo, whatsapp, cib, fonte = _grafo_e_estado({}, [_json_pedido("exame zzz inexistente")])
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre o exame zzz inexistente?"))
    assert r1["coleta_pendente"] == ca.PEND_FORMA
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    r2 = await grafo.ainvoke(_entrada("não sei o nome direito") | memoria)
    assert r2["coleta_pendente"] == ca.PEND_EQUIPE and whatsapp.enviados[-1] == ca.TEXTO_OFERTA_EQUIPE
    assert _starts(cib) == 0 and len(fonte.pedidos) <= 1, "o 'nao sei' nunca vira busca"
    memoria = {k: r2[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    await grafo.ainvoke(_entrada("Quero sim, por favor!") | memoria)
    assert _starts(cib) == 1


async def test_grafo_deixa_pra_la_encerra_sem_busca() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado, _json_pedido
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

    grafo, whatsapp, cib, fonte = _grafo_e_estado({}, [_json_pedido("exame zzz inexistente")])
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre o exame zzz inexistente?"))
    antes = len(fonte.pedidos)
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    r2 = await grafo.ainvoke(_entrada("deixa pra lá") | memoria)
    assert whatsapp.enviados[-1] == ca.TEXTO_ASSUNTO_ENCERRADO and not r2.get("coleta_pendente")
    assert len(fonte.pedidos) == antes and _starts(cib) == 0


async def test_grafo_coparticipacao_do_procedimento_tem_a_resposta_honesta() -> None:
    from tests.unit.agents.test_helena_cobertura_assistida import _grafo_e_estado
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    grafo, whatsapp, _, fonte = _grafo_e_estado({}, [json.dumps({"procedimentos": ["tomografia"]})])
    await grafo.ainvoke(_entrada("Quanto vou pagar de coparticipação numa tomografia?"))
    assert whatsapp.enviados[-1] == ca.TEXTO_COPARTICIPACAO and fonte.pedidos == []
