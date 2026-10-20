"""Cobertura assistida (DL-0128): extracao, traducao para o nome oficial, opcoes, para quem, 2 tentativas,
equipe so' com "sim", textos de situacao e os casos A-E. Casos GENERICOS (nenhum termo do teste do dono)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_fluxo import (
    PEND_RESPONDIDO,
    Dependencias,
    FluxoDeCobertura,
    quem_pelo_texto,
)
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.conhecimento.catalogo_procedimentos import (
    BuscaDeProcedimentos,
    Procedimento,
    carregar_sinonimos,
    expandir,
)


@dataclass(frozen=True)
class _Car:
    cumprida: bool | None
    dias: int | None = 0
    termina_em: str | None = None


@dataclass(frozen=True)
class _Item:
    descricao: str
    situacao: str = "coberto_rol"
    tem_diretriz: bool = False
    rol_edicao: str | None = "ROL 2021 - RN Nº 465/2021"
    carencias: tuple[_Car, ...] = ()


@dataclass(frozen=True)
class _View:
    itens: tuple[_Item, ...]
    segmentacao: str | None = "Ambulatorial + Hospitalar"
    truncada: bool = False
    fonte_atualizada_em: str | None = "2026-10-18T10:00:00Z"


_CATALOGO = (
    Procedimento("40808041", "TC TORAX", "Tomografia computadorizada de tórax"),
    Procedimento("40808130", "TC ABDOME TOTAL", "Tomografia computadorizada do abdome total"),
    Procedimento("40901122", "US ABDOME TOTAL", "Ultrassonografia do abdome total"),
    Procedimento("40901114", "US ABDOME SUPERIOR", "Ultrassonografia do abdome superior"),
    Procedimento("40304361", "HEMOGRAMA", "Hemograma com contagem de plaquetas"),
    Procedimento("31005497", "COLECISTECTOMIA", "Colecistectomia com ou sem colangiografia"),
    Procedimento("40101010", "ECG", "Eletrocardiograma convencional"),
)


@dataclass
class _Mundo:
    pedido: ca.Pedido | None = None
    coberturas: dict[str, Any] = field(default_factory=dict)
    graus: list[str] = field(default_factory=list)
    nomes: dict[int, str] = field(default_factory=dict)
    elegibilidade: dict[str, Any] | None = None
    consultas: list[str] = field(default_factory=list)
    falhar: bool = False
    extracoes: int = 0

    async def extrair(self, texto: str) -> ca.Pedido | None:
        # Como o modelo: na 1a mensagem, o pedido do teste; depois, o nome que a pessoa escreveu.
        self.extracoes += 1
        if self.extracoes == 1 or self.pedido is None:
            return self.pedido
        return ca.Pedido(procedimentos=(texto,), pelo_modelo=True)

    async def cobertura(self, procedimento: str) -> Any:
        self.consultas.append(procedimento)
        if self.falhar:
            return None
        return self.coberturas.get(procedimento, _View(()))

    async def dependentes(self) -> tuple[list[str], bool, dict[int, str]]:
        return self.graus, bool(self.nomes), self.nomes

    async def eleg(self) -> dict[str, Any] | None:
        return self.elegibilidade

    async def geral(self) -> str:
        return "No cadastro, seu plano consta com segmentação Ambulatorial."


def _busca() -> BuscaDeProcedimentos:
    busca = BuscaDeProcedimentos(transporte=None, sinonimos=carregar_sinonimos())
    busca.carregar(_CATALOGO, "v1")
    return busca


def _fluxo(m: _Mundo, *, busca: BuscaDeProcedimentos | None = None) -> FluxoDeCobertura:
    return FluxoDeCobertura(
        Dependencias(
            extrair=m.extrair,
            cobertura=m.cobertura,
            dependentes=m.dependentes,
            elegibilidade=m.eleg,
            busca=busca if busca is not None else _busca(),
            pergunta_para_quem=lambda graus, com_nome: f"Antes de eu consultar: é para quem? ({len(graus)})",
            texto_geral=m.geral,
        )
    )


def _pedido(*procs: str, **kw: Any) -> ca.Pedido:
    return ca.Pedido(procedimentos=procs, pelo_modelo=True, **kw)


# --- extracao ---
def test_o_pedido_do_modelo_e_lido_mesmo_com_texto_em_volta() -> None:
    bruto = (
        'Claro: {"procedimentos": ["tomografia do torax"], "tipo": "procedimento", "assunto": "cobertura",'
    )
    bruto += ' "urgencia": false, "para_quem": "titular", "quem_texto": "", "recem_nascido": false,'
    bruto += ' "exclusao_legal": "nenhuma", "discorda": false}'
    pedido = ca.pedido_do_modelo(bruto)
    assert (
        pedido is not None
        and pedido.procedimentos == ("tomografia do torax",)
        and pedido.para_quem == "titular"
    )


@pytest.mark.parametrize("bruto", ["", "nao sei", '{"tipo": 3}', "[1, 2]"])
def test_resposta_invalida_do_modelo_nao_quebra(bruto: str) -> None:
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is None or pedido.procedimentos == ()


def test_dominios_fechados_do_pedido() -> None:
    bruto = json.dumps({"procedimentos": ["x" * 200, "ok exame"], "tipo": "foguete", "para_quem": "marte"})
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is not None and pedido.tipo == "procedimento" and pedido.para_quem == "nao_disse"
    assert pedido.procedimentos == ("ok exame",)


def test_reserva_deterministica() -> None:
    p = ca.pedido_deterministico("é urgente, minha mãe precisa de um remédio", [])
    assert p.urgencia and p.tipo == "remedio" and p.para_quem == "terceiro"


# --- traducao para o nome oficial ------------------------------------------------------------------------
def test_os_sinonimos_tem_fonte_e_expandem_o_termo() -> None:
    sinonimos = carregar_sinonimos()
    assert sinonimos and all(s.fonte for s in sinonimos)
    assert "ultrassonografia" in expandir("ultrassom da barriga", sinonimos)


async def test_o_lexico_com_sinonimo_acha_o_nome_oficial() -> None:
    resultado = await _busca().buscar("cirurgia de vesícula")
    assert resultado.candidatos and resultado.candidatos[0].procedimento.codigo == "31005497"


async def test_termo_generico_da_varias_opcoes() -> None:
    resultado = await _busca().buscar("ultrassom do abdome")
    nomes = [c.procedimento.codigo for c in resultado.candidatos]
    assert {"40901122", "40901114"} <= set(nomes) and not resultado.certo


# --- fluxo --------------------------------------------------------------------------------------------------
async def test_termo_antes_de_cobre_consulta_pelo_codigo() -> None:
    m = _Mundo(
        pedido=_pedido("tomografia computadorizada de tórax", para_quem="titular"),
        coberturas={"40808041": _View((_Item("TC TORAX"),))},
    )
    up = await _fluxo(m).responder(
        "O médico pediu tomografia computadorizada de tórax. Meu plano cobre?", "", ""
    )
    assert m.consultas == ["40808041"]
    assert "consta no Rol da ANS" in up["response_text"]
    assert "dados do cadastro de 18/10/2026" in up["response_text"]
    assert "não consigo confirmar cobertura de um procedimento específico" not in up["response_text"]
    assert up["coleta_pendente"] == PEND_RESPONDIDO


async def test_varias_opcoes_e_a_escolha_pelo_numero() -> None:
    m = _Mundo(
        pedido=_pedido("ultrassom do abdome", para_quem="titular"),
        coberturas={"40901114": _View((_Item("US ABDOME SUPERIOR"),))},
    )
    fluxo = _fluxo(m)
    up = await fluxo.responder("o plano cobre ultrassom do abdome?", "", "")
    assert up["coleta_pendente"] == ca.PEND_OPCOES
    assert "1) " in up["response_text"] and "me diga o nome exato" not in up["response_text"]
    ctx = ca.Contexto.decodificar(up["coleta_contexto"])
    numero = 1 + [c for c, _ in ctx.opcoes].index("40901114")
    up2 = await fluxo.responder(str(numero), up["coleta_pendente"], up["coleta_contexto"])
    assert m.consultas[-1] == "40901114" and "consta no Rol" in up2["response_text"]


async def test_duas_falhas_e_a_oferta_da_equipe() -> None:
    m = _Mundo(pedido=_pedido("exame inexistente xyz", para_quem="titular"))
    fluxo = _fluxo(m)
    up = await fluxo.responder("cobre exame inexistente xyz?", "", "")
    assert up["coleta_pendente"] == ca.PEND_FORMA and "pedido médico" in up["response_text"]
    up2 = await fluxo.responder("outro nome qualquer zzz", up["coleta_pendente"], up["coleta_contexto"])
    assert up2["coleta_pendente"] == ca.PEND_EQUIPE
    assert up2["response_text"] == ca.TEXTO_OFERTA_EQUIPE
    resumo = ca.resumo_para_a_equipe(ca.Contexto.decodificar(up2["coleta_contexto"]))
    assert "exame inexistente xyz" in resumo and "outro nome qualquer zzz" in resumo
    up3 = await fluxo.responder("não", up2["coleta_pendente"], up2["coleta_contexto"])
    assert up3["response_text"] == ca.TEXTO_SEM_EQUIPE and up3["coleta_pendente"] is None


async def test_falha_da_amh_nunca_vira_nao_encontrei() -> None:
    m = _Mundo(pedido=_pedido("hemograma", para_quem="titular"), falhar=True)
    fluxo = _fluxo(m)
    up = await fluxo.responder("cobre hemograma?", "", "")
    assert up["response_text"] == ca.TEXTO_FALHA_DA_CONSULTA and up["coleta_pendente"] == ca.PEND_FALHA
    m.falhar = False
    m.coberturas = {"40304361": _View((_Item("HEMOGRAMA"),))}
    up2 = await fluxo.responder("tentar de novo", up["coleta_pendente"], up["coleta_contexto"])
    assert "consta no Rol" in up2["response_text"]


async def test_varios_procedimentos_cada_um_em_lista() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma", "eletrocardiograma", para_quem="titular"),
        coberturas={
            "40304361": _View((_Item("HEMOGRAMA"),)),
            "40101010": _View((_Item("ECG", situacao="coberto_extra"),)),
        },
    )
    up = await _fluxo(m).responder("cobre hemograma e eletrocardiograma?", "", "")
    assert "1) " in up["response_text"] and "2) " in up["response_text"]


@pytest.mark.parametrize(
    ("pedido", "esperado"),
    [
        (_pedido(tipo="remedio"), ca.TEXTO_REMEDIO),
        (_pedido(tipo="material"), ca.TEXTO_MATERIAL),
        (_pedido("x procedimento", exclusao_legal="estetica"), ca.TEXTO_EXCLUSAO["estetica"]),
        (_pedido("x procedimento", exclusao_legal="inseminacao"), ca.TEXTO_EXCLUSAO["inseminacao"]),
        (_pedido("x procedimento", para_quem="terceiro"), ca.TEXTO_TERCEIRO),
        (_pedido("x procedimento", recem_nascido=True), ca.TEXTO_RECEM_NASCIDO),
        (_pedido("x procedimento", assunto="reembolso"), ca.TEXTO_REEMBOLSO),
    ],
)
async def test_casos_com_regra_propria_oferecem_a_equipe(pedido: ca.Pedido, esperado: str) -> None:
    texto = "pergunta so por estetica, inseminacao artificial"
    up = await _fluxo(_Mundo(pedido=pedido)).responder(texto, "", "")
    assert up["response_text"] == esperado and up["coleta_pendente"] == ca.PEND_EQUIPE


async def test_coparticipacao_e_desvios() -> None:
    up = await _fluxo(_Mundo(pedido=_pedido(assunto="coparticipacao"))).responder("quanto pago?", "", "")
    assert up["response_text"] == ca.TEXTO_COPARTICIPACAO
    assert await _fluxo(_Mundo(pedido=_pedido(assunto="autorizacao"))).responder("x", "", "") == {
        "desviar_para": "autorizacao"
    }
    rede = _fluxo(_Mundo(pedido=_pedido(assunto="rede")))
    assert await rede.responder("onde faco esse exame?", "", "") == {"desviar_para": "rede"}


async def test_urgencia_e_discordancia_vem_primeiro() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma", para_quem="titular", urgencia=True, discorda=True),
        coberturas={"40304361": _View((_Item("HEMOGRAMA"),))},
    )
    up = await _fluxo(m).responder("é urgente", "", "")
    assert up["response_text"].startswith(ca.PREFIXO_URGENCIA + ca.PREFIXO_DISCORDA)


@pytest.mark.parametrize(
    ("item", "trecho"),
    [
        (_Item("COLECISTECTOMIA", situacao="fora_da_segmentacao"), "não faz parte da segmentação"),
        (_Item("COLECISTECTOMIA", situacao="fora_do_rol"), "não é uma negativa"),
        (_Item("COLECISTECTOMIA", situacao="indeterminado"), "não é uma negativa"),
        (_Item("COLECISTECTOMIA", tem_diretriz=True), "diretriz de utilização"),
        (_Item("COLECISTECTOMIA", carencias=(_Car(False, 30, "2027-01-10"),)), "carência até 10/01/2027"),
    ],
)
async def test_textos_de_situacao_nunca_terminam_em_nao_cobre(item: _Item, trecho: str) -> None:
    m = _Mundo(
        pedido=_pedido("cirurgia de vesícula", para_quem="titular"), coberturas={"31005497": _View((item,))}
    )
    up = await _fluxo(m).responder("cobre cirurgia de vesícula?", "", "")
    assert trecho in up["response_text"]
    assert not up["response_text"].rstrip(".").lower().endswith("não cobre")


async def test_pergunta_repetida_nao_repete_o_texto() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma", para_quem="titular"),
        coberturas={"40304361": _View((_Item("HEMOGRAMA"),))},
    )
    fluxo = _fluxo(m)
    up = await fluxo.responder("cobre hemograma?", "", "")
    up2 = await fluxo.responder("cobre hemograma?", up["coleta_pendente"], up["coleta_contexto"])
    assert (
        up2["response_text"].startswith("Já te passei o que consta")
        and up2["coleta_pendente"] == ca.PEND_EQUIPE
    )


async def test_para_quem_com_dependentes_e_resposta_pelo_nome() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma"),
        graus=["filho ou filha", "cônjuge"],
        nomes={1: "joana", 2: "carlos"},
        coberturas={"40304361": _View((_Item("HEMOGRAMA"),))},
    )
    fluxo = _fluxo(m)
    up = await fluxo.responder("cobre hemograma?", "", "")
    assert up["coleta_pendente"] == ca.PEND_QUEM
    up2 = await fluxo.responder("é pra Joana", up["coleta_pendente"], up["coleta_contexto"])
    assert "consta no Rol" in up2["response_text"] and "carência do dependente" in up2["response_text"]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", "titular"),
        ("pra mim", "titular"),
        ("3", "dependente:2"),
        ("é pro meu filho", "dependente:1"),
        ("pra minha esposa", "dependente:2"),
        ("é pra minha mãe", "terceiro"),
        ("pro meu neto", "fora_do_plano"),
        ("carlos", "dependente:2"),
    ],
)
def test_quem_pelo_texto(texto: str, esperado: str) -> None:
    assert quem_pelo_texto(texto, ["filho ou filha", "cônjuge"], {1: "joana", 2: "carlos"}) == esperado


async def test_plano_sem_atendimento_avisa_com_cuidado() -> None:
    m = _Mundo(
        pedido=_pedido("hemograma", para_quem="titular"),
        coberturas={"40304361": _View((_Item("HEMOGRAMA"),))},
        elegibilidade={"vinculos": [{"atendimento_liberado": False, "situacao_beneficiario": "Suspenso"}]},
    )
    up = await _fluxo(m).responder("cobre hemograma?", "", "")
    assert (
        "não está liberado agora (situação: Suspenso)" in up["response_text"]
        and "Lucas" in up["response_text"]
    )


async def test_sem_catalogo_a_busca_por_texto_da_opcoes_pela_descricao() -> None:
    m = _Mundo(
        pedido=_pedido("raio x de coluna", para_quem="titular"),
        coberturas={
            "raio x de coluna": _View((_Item("RX COLUNA CERVICAL"), _Item("RX COLUNA LOMBAR"))),
            "RX COLUNA LOMBAR": _View((_Item("RX COLUNA LOMBAR"), _Item("RX COLUNA LOMBAR COM FLEXAO"))),
        },
    )
    vazio = BuscaDeProcedimentos(transporte=None)
    fluxo = _fluxo(m, busca=vazio)
    up = await fluxo.responder("cobre raio x de coluna?", "", "")
    assert up["coleta_pendente"] == ca.PEND_OPCOES
    up2 = await fluxo.responder("o da lombar", up["coleta_pendente"], up["coleta_contexto"])
    assert "rx coluna lombar: consta" in up2["response_text"].lower()


async def test_sem_procedimento_na_pergunta_geral() -> None:
    up = await _fluxo(_Mundo(pedido=_pedido())).responder("o que meu plano cobre?", "", "")
    assert "segmentação" in up["response_text"] and up["coleta_pendente"] == ca.PEND_FORMA


def test_os_textos_passam_nas_cercas_do_envio() -> None:
    textos = [
        getattr(ca, n)
        for n in dir(ca)
        if n.startswith(("TEXTO_", "PREFIXO_")) and isinstance(getattr(ca, n), str)
    ]
    textos += list(ca.TEXTO_EXCLUSAO.values())
    textos += [ca.pergunta_das_opcoes("x", ["A", "B"]), ca.pergunta_da_forma("x")]
    # DL-0130: o aviso de CPT, com e sem data.
    textos += [ca.TEXTO_CPT.format(vigencia="válida até 01/03/2027"), ca.TEXTO_CPT.format(vigencia="vigente")]
    for t in textos:
        assert motivo_de_recusa(t, "inform", start_aconteceu=False) is None, t
        assert motivo_de_canal_nao_confirmado(t) is None, t


def test_contexto_ida_e_volta() -> None:
    ctx = ca.Contexto(termos=["a"], quem="titular", tentativas=1, opcoes=[("1", "X")], tentados=["a"])
    assert ca.Contexto.decodificar(ctx.codificar()) == ctx
    assert ca.Contexto.decodificar("lixo") == ca.Contexto()


@pytest.mark.parametrize(
    ("texto", "esperado"), [("2", 1), ("a segunda", 1), ("nenhuma", -1), ("lombar", 1), ("xyz", None)]
)
def test_escolha_da_opcao(texto: str, esperado: int | None) -> None:
    assert ca.escolha_da_opcao(texto, [("1", "RX COLUNA CERVICAL"), ("2", "RX COLUNA LOMBAR")]) == esperado


# --- grafo (ponta a ponta, sem AWS) -------------------------------------------------------------------------
class _FonteGrafo:
    def __init__(self, coberturas: dict[str, Any]) -> None:
        self.coberturas = coberturas
        self.pedidos: list[str] = []

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        return None

    async def consultar_cobertura(self, ref: str, procedimento: str) -> Any:
        self.pedidos.append(procedimento)
        return self.coberturas.get(procedimento, _View(()))


def _grafo_e_estado(coberturas: dict[str, Any], respostas: list[str]) -> tuple[Any, Any, Any, _FonteGrafo]:
    from datetime import UTC, datetime

    from maezo.agents.helena.graph import HelenaGraph
    from maezo.conhecimento import saude_gerativa as sg
    from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
    from maezo.tools.workers.dmn_transport import FakeDmnTransport
    from tests.support.audit_fakes import FakeStartAuditSink
    from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _Modelo, _WhatsApp
    from tests.unit.agents.test_helena_saude_gerativa import _Conversa, _Transporte

    fonte = _FonteGrafo(coberturas)
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=_Modelo(respostas),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 19, 15, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        catalogo_procedimentos=_busca(),
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
    )
    return grafo.compile_graph().compile(), whatsapp, cib, fonte


def _json_pedido(*procs: str) -> str:
    return json.dumps({"procedimentos": list(procs), "tipo": "procedimento", "assunto": "cobertura",
                       "urgencia": False, "para_quem": "titular", "quem_texto": "", "recem_nascido": False,
                       "exclusao_legal": "nenhuma", "discorda": False})  # fmt: skip


async def test_grafo_termo_antes_de_cobre_vai_ao_codigo_oficial() -> None:
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

    grafo, whatsapp, _, fonte = _grafo_e_estado(
        {"40808041": _View((_Item("TC TORAX"),))}, [_json_pedido("tomografia computadorizada de tórax")]
    )
    await grafo.ainvoke(_entrada("O médico pediu tomografia computadorizada de tórax. Meu plano cobre?"))
    assert fonte.pedidos == ["40808041"]
    assert "consta no Rol da ANS" in whatsapp.enviados[-1]
    assert "procedimento específico" not in whatsapp.enviados[-1]


async def test_grafo_duas_falhas_e_o_sim_abre_o_caso_com_o_resumo() -> None:
    from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _starts

    grafo, whatsapp, cib, _ = _grafo_e_estado({}, [_json_pedido("exame zzz inexistente")])
    r1 = await grafo.ainvoke(_entrada("Meu plano cobre o exame zzz inexistente?"))
    assert r1["coleta_pendente"] == ca.PEND_FORMA and _starts(cib) == 0
    memoria = {k: r1[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    r2 = await grafo.ainvoke(_entrada("qqqq wwww", **{}) | memoria)
    assert r2["coleta_pendente"] == ca.PEND_EQUIPE and _starts(cib) == 0, "sem o sim, nenhum caso"
    assert whatsapp.enviados[-1] == ca.TEXTO_OFERTA_EQUIPE
    memoria = {k: r2[k] for k in ("coleta_pendente", "coleta_contexto", "historico_conversa")}
    await grafo.ainvoke(_entrada("sim", **{}) | memoria)
    assert _starts(cib) == 1


_RAIZ_OPENAPI = __import__("pathlib").Path(__file__).resolve().parents[3] / "config/integrations/amh/openapi"
_TINA = (_RAIZ_OPENAPI / "tina.openapi.yaml").read_bytes()
_PROCS = (_RAIZ_OPENAPI / "tina-procedimentos.openapi.yaml").read_bytes()


async def test_adaptador_real_le_o_catalogo_pelo_contrato_pinado() -> None:
    from maezo.adapters.amh import tina as tina_mod
    from maezo.ports.errors import PortResult

    corpo = {
        "versao_catalogo": "a" * 64,
        "pagina": 1,
        "total_paginas": 1,
        "total_itens": 1,
        "itens": [
            {"codigo": "40808041", "descricao": "TC TORAX", "nome_tuss": "Tomografia de tórax", "grupo": None}
        ],
        "fonte_atualizada_em": "2026-10-19T12:00:00Z",
    }

    class _Executor(tina_mod.GovernedTinaExecutor):
        registered_operations = frozenset({tina_mod.OP_PROCEDIMENTOS})

        def __init__(self) -> None:
            self.pedidos: list[Any] = []

        async def execute(self, request: Any) -> Any:
            self.pedidos.append(request)
            return PortResult.ok(tina_mod.GovernedTinaResponse(200, json.dumps(corpo).encode("utf-8")))

    executor = _Executor()
    adaptador = tina_mod.AmhTinaAdapter(
        _TINA, executor=executor, procedimentos_openapi_bytes=_PROCS, procedimentos_executor=executor
    )
    r = await adaptador.get_catalogo_procedimentos(
        purpose_of_use="atendimento_whatsapp", consent_decision_ref="referencia", pagina=1, tamanho=2000
    )
    assert r.succeeded and r.value is not None and r.value.itens[0].tuss == "Tomografia de tórax"
    [pedido] = executor.pedidos
    assert pedido.path == "/interop/tina/v1/procedimentos"
    assert dict(pedido.query) == {"purpose_of_use": "atendimento_whatsapp", "pagina": 1, "tamanho": 2000}


def test_os_sinonimos_sao_achados_pelo_spec_dir_e_vao_na_imagem() -> None:
    from pathlib import Path

    from maezo.conhecimento.catalogo_procedimentos import raiz_dos_sinonimos

    assert raiz_dos_sinonimos().is_file()
    raiz = Path(__file__).resolve().parents[3]
    assert '"spec/conhecimento/cobertura" = "maezo/spec/conhecimento/cobertura"' in (
        raiz / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert "COPY spec/conhecimento/cobertura" in (raiz / "deploy/Dockerfile").read_text(encoding="utf-8")


def test_lexico_por_raiz_nao_confunde_palavras_parecidas() -> None:
    from maezo.conhecimento.catalogo_procedimentos import _casa_palavra

    assert _casa_palavra("cranio", "craniana")
    assert not _casa_palavra("eletrocardiograma", "eletroencefalograma")


def test_sinais_do_modelo_so_valem_com_apoio_no_texto() -> None:
    p = ca.Pedido(procedimentos=("x",), exclusao_legal="estetica", assunto="rede", para_quem="titular")
    q = ca.confirmar_pelo_texto(p, "meu plano cobre esse procedimento? como faco pelo plano?")
    assert (q.exclusao_legal, q.assunto, q.para_quem) == ("nenhuma", "cobertura", "nao_disse")
    r = ca.confirmar_pelo_texto(p, "e so por estetica, onde faco? e para mim")
    assert (r.exclusao_legal, r.assunto, r.para_quem) == ("estetica", "rede", "titular")


def test_o_termo_extraido_perde_o_contexto_e_o_que_foi_negado() -> None:
    bruto = json.dumps({"procedimentos": ["mensalidade se ultrassom da tireoide"], "tipo": "procedimento"})
    pedido = ca.pedido_do_modelo(bruto)
    assert pedido is not None and pedido.procedimentos == ("ultrassom da tireoide",)


def test_nome_legivel() -> None:
    assert ca.nome_legivel("RX COLUNA LOMBAR") == "Rx coluna lombar"
    assert len(ca.nome_legivel("Ultrassonografia " * 10)) <= 93


async def test_parecidos_com_a_mesma_resposta_nao_viram_opcoes() -> None:
    m = _Mundo(
        pedido=_pedido("ultrassom do abdome", para_quem="titular"),
        coberturas={
            "40901122": _View((_Item("US ABDOME TOTAL"),)),
            "40901114": _View((_Item("US ABDOME SUPERIOR"),)),
        },
    )
    up = await _fluxo(m).responder("o plano cobre ultrassom do abdome para mim?", "", "")
    assert up["coleta_pendente"] == PEND_RESPONDIDO and "consta no Rol" in up["response_text"]
    assert " / " in up["response_text"]


async def test_sem_dizer_para_quem_e_com_dependentes_pergunta_antes_das_opcoes() -> None:
    m = _Mundo(pedido=_pedido("ultrassom do abdome", para_quem="titular"), graus=["filho ou filha"])
    up = await _fluxo(m).responder("meu plano cobre ultrassom do abdome?", "", "")
    assert up["coleta_pendente"] == ca.PEND_QUEM and m.consultas == []
