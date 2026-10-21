"""Nome completo dos dependentes do PROPRIO titular verificado (decisao do dono e DPO de 19/10/2026, DL-0127).

* chave `MAEZO_HELENA_DEPENDENTES_NOME` desligada: a resposta de antes, byte a byte (sem nomes);
* ligada: a lista de dependentes leva o nome completo de cada um, so' no ENVIO (estado, historico e log
  levam o marcador); falha da rota nominal, lista truncada, contagem diferente ou nome fora do padrao = o
  texto de sempre;
* o "para quem e'?" da cobertura leva o nome de cada dependente;
* o adaptador recusa ordem quebrada e nome para quem nao e' titular; o relatorio do simulador mascara o nome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.adapters.amh import tina as tina_mod
from maezo.agents.helena import cobertura_rol as cr
from maezo.agents.helena import consultas_plano as cp
from maezo.agents.helena.graph import HelenaGraph
from maezo.ports.tina import DependenteAnonimo, DependenteNominal, DependentesNominaisView, DependentesView
from maezo.qa.simulador.mascaramento import TOKEN_NOME_DEPENDENTE, Mascarador
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_helena_rodada_3_simulador import REF, _classify, _entrada, _Modelo, _WhatsApp

_SEM_NOMES = "Por segurança, este canal não mostra nomes nem outros dados dos dependentes."
_ANA = "Ana Maria de Souza Teste"
_JOAO = "João d'Ávila Teste"


def _anonima(*relacoes: str) -> DependentesView:
    return DependentesView(
        portable_subject_ref=REF,
        eh_titular=True,
        total_ativos=len(relacoes),
        lista_truncada=False,
        dependentes=tuple(
            DependenteAnonimo(relacao_dependencia=r, situacao_vinculo="Apto", rn_no_plano=False)
            for r in relacoes
        ),
        fonte_atualizada_em="2026-10-19",
    )


def _nominal(*itens: tuple[str, str], ref: str = REF, truncada: bool = False) -> DependentesNominaisView:
    return DependentesNominaisView(
        portable_subject_ref=ref,
        eh_titular=True,
        dependentes=tuple(
            DependenteNominal(ordem=i, relacao_dependencia=r, nome_completo=n)
            for i, (n, r) in enumerate(itens, start=1)
        ),
        lista_truncada=truncada,
        fonte_atualizada_em="2026-10-19T10:00:00Z",
    )


class _Fonte:
    def __init__(self, anonima: Any, nominal: Any) -> None:
        self.anonima = anonima
        self.nominal = nominal
        self.chamadas: list[str] = []
        self.refs_da_solicitacao: list[str] = []

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append(subtipo)
        return self.anonima if subtipo == "dependentes" else None

    async def consultar_dependentes_nominais(self, portable_subject_ref: str, request_ref: str) -> Any:
        self.chamadas.append("nomes")
        self.refs_da_solicitacao.append(request_ref)
        return self.nominal


def _grafo(fonte: _Fonte, *, ligada: bool = True) -> tuple[Any, _WhatsApp]:
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=_Modelo([_classify(intent="information")]),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=fonte,  # type: ignore[arg-type]
        relogio=lambda: datetime(2026, 10, 19, 15, 0, tzinfo=UTC),
        helena_dependentes_nome_enabled=ligada,
    )
    return grafo.compile_graph().compile(), whatsapp


async def test_com_a_chave_a_lista_leva_o_nome_completo_so_no_envio() -> None:
    fonte = _Fonte(
        _anonima("Filho(a) até 23 anos", "Cônjuge"),
        _nominal((_ANA, "Filho(a) até 23 anos"), (_JOAO, "Cônjuge")),
    )
    grafo, whatsapp = _grafo(fonte)
    resultado = await grafo.ainvoke(_entrada("quem são meus dependentes?"))
    [enviado] = whatsapp.enviados
    assert enviado == (
        f"No seu cadastro constam 2 dependentes ativos:\n1) {_ANA} (filho ou filha)\n2) {_JOAO} (cônjuge)"
    )
    assert _SEM_NOMES not in enviado
    # Estado e historico: so' o marcador, nunca o nome.
    assert _ANA not in str(resultado.get("response_text"))
    assert cp.tem_marcador_de_dependente(resultado.get("response_text"))
    assert _ANA not in str(resultado.get("historico_conversa"))
    assert fonte.refs_da_solicitacao and all(len(r) >= 8 for r in fonte.refs_da_solicitacao)


async def test_um_dependente() -> None:
    fonte = _Fonte(_anonima("Filho(a) até 23 anos"), _nominal((_ANA, "Filho(a) até 23 anos")))
    grafo, whatsapp = _grafo(fonte)
    await grafo.ainvoke(_entrada("tenho dependentes?"))
    assert whatsapp.enviados == [f"No seu cadastro consta 1 dependente ativo:\n1) {_ANA} (filho ou filha)"]


async def test_sem_a_chave_a_resposta_de_antes() -> None:
    fonte = _Fonte(_anonima("Filho(a) até 23 anos"), _nominal((_ANA, "Filho(a) até 23 anos")))
    grafo, whatsapp = _grafo(fonte, ligada=False)
    await grafo.ainvoke(_entrada("tenho dependentes?"))
    assert "nomes" not in fonte.chamadas
    assert _SEM_NOMES in whatsapp.enviados[0] and _ANA not in whatsapp.enviados[0]


@pytest.mark.parametrize(
    "nominal",
    [
        None,  # falha da rota nominal
        _nominal((_ANA, "Filho(a) até 23 anos"), truncada=True),
        _nominal((_ANA, "Filho(a) até 23 anos"), (_JOAO, "Cônjuge")),  # contagem diferente da anonima
        _nominal(("1234", "Filho(a) até 23 anos")),  # nome fora do padrao
        _nominal((_ANA, "Filho(a) até 23 anos"), ref="amh:psr:v1:outro"),  # outro sujeito
    ],
)
async def test_qualquer_duvida_volta_ao_texto_sem_nomes(nominal: Any) -> None:
    fonte = _Fonte(_anonima("Filho(a) até 23 anos"), nominal)
    grafo, whatsapp = _grafo(fonte)
    await grafo.ainvoke(_entrada("tenho dependentes?"))
    assert _SEM_NOMES in whatsapp.enviados[0]
    assert "{{" not in whatsapp.enviados[0] and _ANA not in whatsapp.enviados[0]


def test_o_envio_sem_nome_no_turno_nunca_mostra_o_marcador() -> None:
    texto = cr.pergunta_para_quem(["filho ou filha"], com_nome=True)
    assert cp.sem_marcador_de_dependente(texto).endswith("2) para dependente 1 (filho ou filha).")
    assert cp.NomesDoTurno({1: _ANA}).preencher(texto).endswith(f"2) para {_ANA} (filho ou filha).")


def test_nomes_do_turno_nao_vazam() -> None:
    import pickle

    nomes = cp.NomesDoTurno({1: _ANA})
    assert _ANA not in repr(nomes) and _ANA not in str(nomes)
    with pytest.raises(TypeError):
        pickle.dumps(nomes)
    assert _ANA not in repr(DependenteNominal(ordem=1, relacao_dependencia=None, nome_completo=_ANA))


class _FonteCobertura(_Fonte):
    async def consultar_cobertura(self, ref: str, procedimento: str) -> Any:
        return None


async def test_para_quem_leva_o_nome_de_cada_dependente() -> None:
    from tests.unit.agents.test_helena_saude_gerativa import _DONO, _Conversa

    conversa = _Conversa()
    fonte = _FonteCobertura(_anonima("Filho(a) até 23 anos"), _nominal((_ANA, "Filho(a) até 23 anos")))
    grafo = HelenaGraph(
        inference=conversa.modelo,  # type: ignore[arg-type]
        dmn=None,  # type: ignore[arg-type]
        cibseven=conversa.cib,
        audit_sink=None,  # type: ignore[arg-type]
        whatsapp=conversa.whatsapp,
        consultas_plano=fonte,  # type: ignore[arg-type]
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=conversa.base,
        saude_gerativa=conversa.redator,
        helena_dependentes_nome_enabled=True,
    )
    estado: Any = {
        "conversation_id": _DONO,
        "message_body": "Meu plano cobre mamografia?",
        "message_ref": "m1",
    }
    pergunta = await grafo._cobertura_do_procedimento(estado, REF)
    assert pergunta is not None
    assert "2) para {{DEPENDENTE:1}} (filho ou filha)" in pergunta["response_text"]
    assert grafo._texto_do_envio(estado, pergunta["response_text"]).endswith(
        f"2) para {_ANA} (filho ou filha)."
    )


# --- adaptador -------------------------------------------------------------------------------------


def _corpo(**kw: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "eh_titular": True,
        "dependentes": [
            {"ordem": 1, "relacao_dependencia": "Filho(a) até 23 anos", "nome_completo": _ANA},
            {"ordem": 2, "relacao_dependencia": None, "nome_completo": _JOAO},
        ],
        "lista_truncada": False,
        "fonte_atualizada_em": "2026-10-19T10:00:00Z",
    }
    corpo.update(kw)
    return corpo


def test_adaptador_monta_a_view_nominal() -> None:
    view = tina_mod._dependentes_nominais(_corpo())
    assert [d.ordem for d in view.dependentes] == [1, 2]
    assert view.dependentes[0].nome_completo == _ANA


@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(dependentes=[{"ordem": 2, "relacao_dependencia": None, "nome_completo": _ANA}]),
        _corpo(eh_titular=False),
    ],
)
def test_adaptador_recusa_ordem_quebrada_e_nome_para_nao_titular(corpo: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        tina_mod._dependentes_nominais(corpo)


async def test_sem_o_artefato_a_leitura_nominal_recusa_fechado() -> None:
    adaptador = object.__new__(tina_mod.AmhTinaAdapter)
    adaptador._dependentes_nome = False  # type: ignore[attr-defined]
    resultado = await adaptador.get_dependentes_nominais(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref="x", request_ref="hel_12345678"
    )
    assert not resultado.succeeded


# --- simulador ------------------------------------------------------------------------------------


def test_o_relatorio_do_simulador_mascara_o_nome_do_dependente() -> None:
    m = Mascarador()
    lista = f"No seu cadastro constam 2 dependentes ativos:\n1) {_ANA} (filho ou filha)\n2) {_JOAO} (cônjuge)"
    saida = m.texto(lista)
    assert _ANA not in saida and _JOAO not in saida and saida.count(TOKEN_NOME_DEPENDENTE) == 2
    pergunta = m.texto(
        "Antes de eu consultar: é para quem? Responda 1) para você (titular); "
        f"2) para {_ANA} (filho ou filha)."
    )
    assert _ANA not in pergunta and "1) para você (titular)" in pergunta
    assert (
        m.texto("1) saber onde marcar consulta pelo plano (x)")
        == "1) saber onde marcar consulta pelo plano (x)"
    )


_RAIZ_OPENAPI = __import__("pathlib").Path(__file__).resolve().parents[3] / "config/integrations/amh/openapi"
_TINA_BYTES = (_RAIZ_OPENAPI / "tina.openapi.yaml").read_bytes()
_NOMES_BYTES = (_RAIZ_OPENAPI / "tina-dependentes-nome.openapi.yaml").read_bytes()


async def test_adaptador_real_le_a_rota_nominal_pelo_contrato_pinado() -> None:
    import json

    from maezo.ports.errors import PortResult

    class _Executor(tina_mod.GovernedTinaExecutor):
        registered_operations = frozenset({tina_mod.OP_DEPENDENTES_NOME})

        def __init__(self) -> None:
            self.pedidos: list[Any] = []

        async def execute(self, request: Any) -> Any:
            self.pedidos.append(request)
            return PortResult.ok(tina_mod.GovernedTinaResponse(200, json.dumps(_corpo()).encode("utf-8")))

    executor = _Executor()
    adaptador = tina_mod.AmhTinaAdapter(
        _TINA_BYTES,
        executor=executor,
        dependentes_nome_openapi_bytes=_NOMES_BYTES,
        dependentes_nome_executor=executor,
    )
    resultado = await adaptador.get_dependentes_nominais(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref="base", request_ref="hel_12345678"
    )
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None and [d.nome_completo for d in resultado.value.dependentes] == [
        _ANA,
        _JOAO,
    ]
    [pedido] = executor.pedidos
    assert pedido.path.endswith("/dependentes/nomes") and pedido.request_ref == "hel_12345678"
    assert dict(pedido.query) == {"purpose_of_use": "atendimento_whatsapp"}
