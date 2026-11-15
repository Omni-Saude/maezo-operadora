"""COBERTURA AMPLA atras da chave (dono, 14/11/2026; DL-0167, HEL-1): o esqueleto da habilidade `cobertura`.

Chave DESLIGADA (o padrao) = o aviso de canais do DL-0154, byte a byte. Ligada, as camadas respondem em ordem;
sem camada que responda (resposta invalida, falha de dependencia), o aviso de canais (falha fechada).
Beneficiario sintetico; nenhum dado pessoal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from maezo.agents.helena import pacote as _pacote
from maezo.agents.helena.graph import HelenaGraph, build
from maezo.agents.helena.habilidades import cobertura as cob
from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.runtime.inference.rota_global import RotaGlobal
from maezo.runtime.pacote_do_agente import PacoteDoAgente, PacoteInvalidoError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.helena_conversa import _Transporte
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _entrada, _starts, _WhatsApp

_PERGUNTAS = (
    "o convênio paga um hemograma pra mim?",
    "o plano cobre ressonância do joelho?",
    "preciso de autorização pra fazer endoscopia?",
)


class _Modelo:
    def __init__(self, n: int = 1) -> None:
        self.roteador = [decisao_v7("cobertura") for _ in range(n)]
        self.outras = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            return self.roteador.pop(0) if self.roteador else ""
        self.outras += 1
        return ""


class _Camada:
    def __init__(self, camada: cob.Camada, resposta: cob.RespostaDaCobertura | None | Exception) -> None:
        self._camada = camada
        self._resposta = resposta
        self.perguntas: list[cob.PerguntaDeCobertura] = []

    @property
    def camada(self) -> cob.Camada:
        return self._camada

    async def responder(self, pergunta: cob.PerguntaDeCobertura) -> cob.RespostaDaCobertura | None:
        self.perguntas.append(pergunta)
        if isinstance(self._resposta, Exception):
            raise self._resposta
        return self._resposta


def _grafo(modelo: _Modelo, cobertura: cob.ConfiguracaoDaCobertura | None) -> tuple[Any, _WhatsApp, Any]:
    whatsapp = _WhatsApp()
    cib = FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 11, 14, 15, 0, tzinfo=UTC),
        rota_global=RotaGlobal(transporte=_Transporte(), conversas=frozenset({_CONVERSA})),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
        cobertura=cobertura,
    )
    return grafo.compile_graph().compile(), whatsapp, cib


def _ligada(*camadas: Any) -> cob.ConfiguracaoDaCobertura:
    config = cob.da_configuracao({cob.CHAVE_DO_GRAFO: True, cob.CHAVE_DAS_CAMADAS: list(camadas)})
    assert config is not None
    return config


# --- Chave desligada: nada muda ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg", [{}, {cob.CHAVE_DO_GRAFO: False}, {cob.CHAVE_DO_GRAFO: "true"}, {cob.CHAVE_DO_GRAFO: 1}]
)
def test_sem_true_explicito_a_habilidade_fica_desligada(cfg: dict[str, Any]) -> None:
    assert cob.da_configuracao(cfg) is None


def test_build_sem_a_chave_compoe_o_grafo_com_a_cobertura_desligada() -> None:
    capturado: dict[str, Any] = {}
    original = HelenaGraph.__init__

    def _espiao(self: HelenaGraph, **kwargs: Any) -> None:
        capturado.update(kwargs)
        original(self, **kwargs)

    HelenaGraph.__init__ = _espiao  # type: ignore[method-assign]
    try:
        build(
            {
                "inference": _Modelo(),
                "dmn": FakeDmnTransport(),
                "cibseven": FakeCibSevenTransport(),
                "whatsapp": _WhatsApp(),
                "audit_sink": FakeStartAuditSink(),
            }
        )
    finally:
        HelenaGraph.__init__ = original  # type: ignore[method-assign]
    assert capturado["cobertura"] is None


@pytest.mark.parametrize("pergunta", _PERGUNTAS)
async def test_desligada_o_roteador_da_cobertura_responde_o_aviso_de_canais_de_hoje(pergunta: str) -> None:
    modelo = _Modelo()
    grafo, whatsapp, cib = _grafo(modelo, None)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada(pergunta))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
    assert modelo.outras == 0 and _starts(cib) == 0
    assert any(e.get("event") == "helena_cobertura_fora" for e in logs)
    assert not any(
        str(e.get("event", "")).startswith("helena_cobertura_") and e["event"] != "helena_cobertura_fora"
        for e in logs
    )


def test_o_pacote_publicado_nao_liga_a_cobertura_ampla() -> None:
    from maezo.agents import resolve_spec_dir

    dados = yaml.safe_load((resolve_spec_dir() / "agents/helena/pacote/configuracao.yaml").read_text())
    assert cob.CHAVE_DO_PACOTE not in dados or dados[cob.CHAVE_DO_PACOTE] is False


# --- O pacote de comportamento liga a chave -------------------------------------------------------------


def _pacote_com(**extra: Any) -> PacoteDoAgente:
    dados = {"formato": _pacote.FORMATO_CONFIGURACAO, **extra}
    return PacoteDoAgente(
        agente="helena",
        versao="t",
        sha256="0" * 64,
        arquivos={_pacote.ARQUIVO_CONFIGURACAO: yaml.safe_dump(dados)},
    )


def test_o_pacote_sem_a_chave_nao_mexe_na_configuracao() -> None:
    class _Carregador:
        def atual(self) -> PacoteDoAgente:
            return _pacote_com()

    saida = _pacote.PacoteDaHelena(_Carregador()).aplicar_no_grafo({})  # type: ignore[arg-type]
    assert cob.CHAVE_DO_GRAFO not in saida


def test_o_pacote_com_a_chave_liga_e_tipo_errado_recusa_o_pacote_inteiro() -> None:
    class _Carregador:
        def atual(self) -> PacoteDoAgente:
            return _pacote_com(cobertura_ampla=True)

    saida = _pacote.PacoteDaHelena(_Carregador()).aplicar_no_grafo({})  # type: ignore[arg-type]
    assert saida[cob.CHAVE_DO_GRAFO] is True
    with pytest.raises(PacoteInvalidoError):
        _pacote.configuracao(_pacote_com(cobertura_ampla="sim"))


# --- Chave ligada: o contrato das camadas ---------------------------------------------------------------


async def test_ligada_sem_camada_e_o_aviso_de_canais_e_a_lacuna_registrada() -> None:
    modelo = _Modelo()
    grafo, whatsapp, _cib = _grafo(modelo, _ligada())
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
    assert any(e.get("event") == "helena_cobertura_lacuna" for e in logs)


async def test_ligada_a_resposta_com_fonte_sai_literal_sem_o_modelo_de_redacao() -> None:
    texto = f"Pela regra geral: esse exame precisa de pedido médico. {cob.FORMULA_FIXA}"
    resposta = cob.RespostaDaCobertura("resposta", texto, (cob.Fonte("regra_k2", "R-TESTE-1", "k2-teste"),))
    camada = _Camada("regra_geral", resposta)
    modelo = _Modelo()
    grafo, whatsapp, cib = _grafo(modelo, _ligada(camada))
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == texto
    assert modelo.outras == 0 and _starts(cib) == 0
    [log] = [e for e in logs if e.get("event") == "helena_cobertura_respondeu"]
    assert log["fontes"] == ["regra_k2:R-TESTE-1@k2-teste"]
    assert camada.perguntas[0].texto == "o plano cobre ressonância?"
    assert "ressonância" not in str(log)  # o texto da pessoa nunca vai a log


@pytest.mark.parametrize(
    "resposta",
    [
        cob.RespostaDaCobertura("resposta", "Sem fonte nenhuma.", ()),
        cob.RespostaDaCobertura("resposta", "   ", (cob.Fonte("regra_k2", "R-1", "v"),)),
        cob.RespostaDaCobertura("esclarecer", None),
        ConnectionError("fora do ar"),
        None,
    ],
)
async def test_ligada_resposta_invalida_ou_falha_cai_no_aviso_de_canais(resposta: Any) -> None:
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(_Camada("regra_geral", resposta)))
    await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA


async def test_ligada_a_camada_regra_geral_vem_antes_da_seu_plano() -> None:
    plano = _Camada("seu_plano", cob.RespostaDaCobertura("esclarecer", "Do seu plano?"))
    geral = _Camada("regra_geral", None)
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(plano, geral))
    await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert len(geral.perguntas) == 1 and len(plano.perguntas) == 1
    assert whatsapp.enviados[-1] == "Do seu plano?"


async def test_ligada_transferir_e_so_a_oferta_sem_caso() -> None:
    camada = _Camada("regra_geral", cob.RespostaDaCobertura("transferir", lacuna="sem_regra"))
    grafo, whatsapp, cib = _grafo(_Modelo(), _ligada(camada))
    resultado = await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == cob.TEXTO_TRANSFERIR
    assert resultado.get("coleta_pendente") == PENDENTE_OFERTA_EQUIPE
    assert _starts(cib) == 0  # caso so' com o "sim" (ADR-0066)


def test_camada_que_nao_cumpre_o_protocolo_e_descartada() -> None:
    class _Torta:
        camada = "seu_caso"

        async def responder(self, pergunta: Any) -> None:
            return None

    config = cob.da_configuracao({cob.CHAVE_DO_GRAFO: True, cob.CHAVE_DAS_CAMADAS: [_Torta(), object()]})
    assert config is not None and config.camadas == ()


# --- As travas de politica sao do esqueleto (revisao independente, B1/B2/D1/D3) ------------------------


@pytest.mark.parametrize(
    "texto",
    [
        f"Sim, o plano cobre ressonância e a cobertura está garantida. {cob.FORMULA_FIXA}",
        f"Não, seu plano não cobre esse exame. {cob.FORMULA_FIXA}",
        f"Você cumpre o critério da diretriz, então pode marcar. {cob.FORMULA_FIXA}",
        f"Tome 500mg de dipirona antes do exame. {cob.FORMULA_FIXA}",
        f"Ligue para 0800 123 4567 para pedir. {cob.FORMULA_FIXA}",
        f"Veja em www.exemplo.com.br antes. {cob.FORMULA_FIXA}",
        f"O seu pedido vai ser autorizado. {cob.FORMULA_FIXA}",
        f"O seu plano libera a ressonância. {cob.FORMULA_FIXA}",
        f"A ressonância é coberta pelo seu plano. {cob.FORMULA_FIXA}",
        f"Pode marcar direto, sem guia. {cob.FORMULA_FIXA}",
        f"Você pode fazer sem pedir nada. {cob.FORMULA_FIXA}",
        f"Seu caso se encaixa na regra. {cob.FORMULA_FIXA}",
        f"Fica coberto pelo plano. {cob.FORMULA_FIXA}",
        "Pela regra geral: esse exame precisa de pedido médico.",  # sem a formula fixa
    ],
)
async def test_texto_de_camada_que_fere_uma_trava_nao_sai(texto: str) -> None:
    resposta = cob.RespostaDaCobertura("resposta", texto, (cob.Fonte("regra_k2", "R-1", "v"),))
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(_Camada("regra_geral", resposta)))
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA
    assert any(e.get("event") == "helena_cobertura_texto_barrado" for e in logs)


async def test_esclarecer_que_afirma_cobertura_nao_sai() -> None:
    camada = _Camada("regra_geral", cob.RespostaDaCobertura("esclarecer", "O plano cobre, mas qual exame?"))
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(camada))
    await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == TEXTO_COBERTURA_FORA


async def test_ligada_o_192_da_gestante_com_urgencia_continua_no_fim() -> None:
    texto = f"Pela regra geral: o parto segue a regra da segmentação obstétrica. {cob.FORMULA_FIXA}"
    resposta = cob.RespostaDaCobertura("resposta", texto, (cob.Fonte("regra_k2", "R-1", "v"),))
    pergunta = "cobre parto? to gravida, e urgente"
    desligado, w_desligado, _ = _grafo(_Modelo(), None)
    await desligado.ainvoke(_entrada(pergunta))
    ligado, w_ligado, _ = _grafo(_Modelo(), _ligada(_Camada("regra_geral", resposta)))
    await ligado.ainvoke(_entrada(pergunta))
    assert "192" in w_desligado.enviados[-1]
    assert w_ligado.enviados[-1].startswith(texto) and "192" in w_ligado.enviados[-1]


async def test_o_sim_depois_da_transferencia_abre_o_caso_de_pessoa() -> None:
    camada = _Camada("regra_geral", cob.RespostaDaCobertura("transferir", lacuna="sem_regra"))
    modelo = _Modelo()
    modelo.roteador.append(decisao_v7("equipe", responde=True))
    grafo, whatsapp, cib = _grafo(modelo, _ligada(camada))
    primeiro = await grafo.ainvoke(_entrada("o plano cobre ressonância?"))
    assert whatsapp.enviados[-1] == cob.TEXTO_TRANSFERIR and _starts(cib) == 0
    await grafo.ainvoke(_entrada("sim") | {"coleta_pendente": primeiro.get("coleta_pendente")})
    assert _starts(cib) == 1


def test_a_marca_do_texto_e_de_uso_unico() -> None:
    texto = f"Pela regra geral: texto de teste. {cob.FORMULA_FIXA}"
    cob._guardar(texto)
    assert cob.e_texto_da_cobertura(texto) is True
    assert cob.e_texto_da_cobertura(texto) is False
