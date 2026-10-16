"""HELENA V4 (DL-0115) — o catalogo de necessidades v4 do dono atras da chave `MAEZO_HELENA_V4`.

As tabelas de alarme sao as REAIS do repositorio (`spec/processes/dmn/*.dmn`), avaliadas pelo leitor
FIRST-hit de `tests/support/dmn_first_hit.py`. O modelo e' um fake com a classificacao GRAVADA de cada
mensagem. Dados sinteticos, sem PII.

LICAO DO TESTE DE 15/10/2026 (triagem v2): conferir "nao abriu caso" nao basta. Aqui cada resposta e'
conferida pelo CONTEUDO util (o que a pessoa disse, onde marcar, prazo, 192, dados mascarados).

  1. especialista: texto da coluna "HOJE" (onde marcar + 14 dias uteis + oferta de pessoa), sem pesquisar,
     sem prometer cobertura, sem nomes; "cobre ressonancia?" fica no caminho de hoje;
  2. carteirinha: plano, segmentacao, vigencia e numero ANS com o codigo MASCARADO; nunca CNS;
  3. sintoma leve: so' perguntas de alerta; "nao" -> a resposta final EXATA do dono; opcao 1 -> como marcar;
     opcao 2 -> caso com resumo legivel; "sim" -> alerta clinico;
  4. as travas de seguranca continuam ANTES (alarme, psicossocial, recem-nascido, pedido de pessoa);
  5. CHAVE DESLIGADA: o grafo de antes, byte a byte.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

from maezo.agents.helena import helena_v4 as v4
from maezo.agents.helena.consultas_plano import INTENT_CONSULTA_PLANO
from maezo.agents.helena.graph import (
    _HELENA_MEMORIA_DE_CONVERSA,
    IDENTIDADE_RECONHECIDA,
    RESPOSTA_COBERTURA_ESPECIFICA,
    HelenaGraph,
    _conduta_clinica,
    new_helena_state,
)
from maezo.agents.helena.prompts import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.agents.helena.triagem_v2 import (
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
    TRIAGEM_V2_DMN_KEY,
)
from maezo.ports.tina import ElegibilidadeView, VinculoPlano
from maezo.qa.simulador import aceite
from maezo.qa.simulador.checagens import frases_longas
from maezo.qa.simulador.mascaramento import sem_acento
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_REPO = Path(__file__).resolve().parents[3]
_CONVERSA = "wa:amh:hk1_helena_v4"
_REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_IDENTIDADE = {
    "portable_subject_ref": _REF,
    "faixa_etaria": "adulto",
    "plano_ativo": True,
    "vigencia_inicio": "2024-03-01",
    "vigencia_fim": None,
    "carencia_vigente": False,
    "titular_ref": None,
}
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_sufficiency",
    TRIAGEM_V2_DMN_KEY,
)

# =================================================================================================
# Fakes
# =================================================================================================


class _DmnReal:
    """As tabelas `.dmn` REAIS (primeiro acerto), no formato que o grafo espera."""

    def __init__(self) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas.append((decision_key, dict(variables)))
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        versao = DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )
        return [veredito.saidas], versao

    def chaves(self) -> list[str]:
        return [chave for chave, _ in self.chamadas]


class _Gravador(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


class _Modelo:
    """O classificador devolve a extracao GRAVADA; qualquer outra chamada recebe `""`."""

    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _vinculo(**sobre: Any) -> VinculoPlano:
    base: dict[str, Any] = {
        "carteirinha_mascarada": "******4567",
        "plano": "PLANO SINTETICO ENFERMARIA",
        "registro_ans_plano": "999999",
        "segmentacao": "Ambulatorial + Hospitalar",
        "acomodacao": "Enfermaria",
        "tipo_contratacao": "Coletivo empresarial",
        "vigencia_inicio": "2024-03-01",
        "cancelamento": None,
        "atendimento_liberado": True,
        "situacao_vinculo": "Ativo",
        "situacao_contrato": "Ativo",
        "titular": True,
        "relacao_dependencia": None,
        "titular_ref": None,
    }
    base.update(sobre)
    return VinculoPlano(**base)


def _elegibilidade(**sobre: Any) -> ElegibilidadeView:
    return ElegibilidadeView(
        portable_subject_ref=_REF,
        ativo=True,
        vinculos=(_vinculo(**sobre),),
        fonte_atualizada_em="2026-10-06T10:00:00Z",
        campos_ausentes=frozenset(),
    )


class _Fonte:
    def __init__(self, view: Any = None) -> None:
        self.chamadas: list[tuple[str, str]] = []
        self._view = view if view is not None else _elegibilidade()

    async def consultar(self, portable_subject_ref: str, subtipo: str) -> Any:
        self.chamadas.append((portable_subject_ref, subtipo))
        return self._view


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return json.dumps(base)


_INFO = _classify()
_SINTOMA_ADULTO = _classify(intent="symptom", population="adult", intensidade="moderada")
_SINTOMA_CRIANCA = _classify(intent="symptom", population="pediatric", intensidade="leve")


class _Conversa:
    """Uma conversa de varios turnos: cada turno e' (mensagem, classificacao gravada); a memoria de conversa
    (coleta, historico, memoria clinica) passa de um turno ao seguinte como o checkpointer faz."""

    def __init__(
        self,
        *,
        v4: bool = True,
        v2: bool = False,
        fonte: Any = None,
        com_identidade: bool = False,
        inicial: dict[str, Any] | None = None,
    ) -> None:
        self.v4 = v4
        self.v2 = v2
        self.fonte = fonte
        self.com_identidade = com_identidade
        self.cib = _Gravador()
        self.dmn = _DmnReal()
        self.whatsapp = _WhatsApp()
        self.modelo = _Modelo([])
        self.memoria: dict[str, Any] = dict(inicial or {})
        self.resultados: list[dict[str, Any]] = []

    def _grafo(self) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=self.dmn,  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=FakeStartAuditSink(),
            whatsapp=self.whatsapp,
            historico_enabled=True,
            coleta_enabled=True,
            triagem_v2_enabled=self.v2,
            helena_v4_enabled=self.v4,
            consultas_plano=self.fonte,
            relogio=lambda: datetime(2026, 10, 16, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    async def diz(self, mensagem: str, extracao: str = _INFO, **estado_extra: Any) -> dict[str, Any]:
        self.modelo._respostas.append(extracao)
        identidade: dict[str, Any] = {}
        if self.com_identidade:
            identidade = {
                "identidade_beneficiario": _IDENTIDADE,
                "identidade_desfecho": IDENTIDADE_RECONHECIDA,
            }
        estado: dict[str, Any] = dict(
            new_helena_state(
                tenant_id="amh",
                conversation_id=_CONVERSA,
                canal="whatsapp",
                beneficiario_pseudo_id="PSEUDO-HELENA-V4",
                message_body=mensagem,
                message_ref="hk1_msg_helena_v4",
                **identidade,
            )
        )
        estado.update(self.memoria)
        estado.update(estado_extra)
        resultado = await self._grafo().ainvoke(estado)
        self.memoria = {k: resultado[k] for k in _HELENA_MEMORIA_DE_CONVERSA if k in resultado}
        self.resultados.append(resultado)
        return cast(dict[str, Any], resultado)

    @property
    def motivos(self) -> list[str]:
        return [v["motivo_categoria"] for v in self.cib.inicios]


def _sem_acento(texto: str) -> str:
    return sem_acento(texto)


# =================================================================================================
# Os lexicos (pequenos, deterministicos)
# =================================================================================================


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("preciso consultar com um cardiologista", ("cardiologista", False)),
        ("onde tem dermatologista pelo plano", ("dermatologista", False)),
        ("quero marcar ortopedista para o meu filho", ("ortopedista", False)),
        ("meu plano cobre consulta com endocrinologista", ("endocrinologista", False)),
        ("preciso de um urologista", ("urologista", False)),
        ("quero um oftalmologista", ("oftalmologista", False)),
        ("preciso de um oculista", ("oftalmologista", False)),
        ("tem neurologista no plano", ("neurologista", False)),
        ("preciso de um otorrino", ("otorrinolaringologista", False)),
        # Consulta BASICA na RN 566 (ginecologia e obstetricia, pediatria, clinica medica): 7 dias uteis.
        ("preciso de um ginecologista", ("ginecologista", True)),
        ("quero marcar pediatra", ("pediatra", True)),
        ("preciso de um clinico geral", ("clínico geral", True)),
    ],
)
def test_especialidade_pedida_reconhece_o_lexico_fechado(plano: str, esperado: tuple[str, bool]) -> None:
    assert v4.especialidade_pedida(plano) == esperado


@pytest.mark.parametrize(
    "plano",
    [
        # Cobertura de PROCEDIMENTO / exame segue o caminho de hoje.
        "meu plano cobre ressonancia",
        "meu plano cobre cirurgia com o ortopedista",
        "preciso de um exame com cardiologista",
        "o resultado do exame que o cardiologista pediu",
        "preciso de encaminhamento para dermatologista",
        # Fora do lexico DE PROPOSITO: saude mental (acolhimento) e prazo de 10 dias uteis.
        "preciso de um psiquiatra",
        "quero um psicologo",
        "preciso de fisioterapia",
        "quero uma nutricionista",
        "preciso de um fonoaudiologo",
        "preciso de um oncologista",
        "preciso de um especialista",
        "qual o valor da mensalidade",
    ],
)
def test_especialidade_pedida_nao_confunde_procedimento_nem_o_que_ficou_de_fora(plano: str) -> None:
    assert v4.especialidade_pedida(plano) is None


@pytest.mark.parametrize(
    ("texto", "eco"),
    [
        ("Estou com dor nas costas desde ontem", "dor nas costas desde ontem"),
        ("Oi, estou com dor nas costas desde ontem.", "dor nas costas desde ontem"),
        ("Tô com dor de garganta", "dor de garganta"),
        ("Tenho dor de cabeça forte", "dor de cabeça forte"),
        ("Meu filho está com tosse", "seu filho está com tosse"),
        ("Minha filha está com febre desde ontem. Tomei dipirona.", "sua filha está com febre desde ontem"),
        ("Acordei com o nariz entupido e espirrando", "acordei com o nariz entupido e espirrando"),
    ],
)
def test_o_eco_devolve_o_que_a_pessoa_disse(texto: str, eco: str) -> None:
    assert v4.eco_do_relato(v4.relato_cru(texto)) == eco


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "meu telefone é 11999998888 e estou com dor",
        "fale com fulano@exemplo.com sobre a dor",
        "[NOME] com dor",
    ],
)
def test_o_eco_nunca_devolve_dado_pessoal_nem_vazio(texto: str) -> None:
    assert v4.eco_do_relato(v4.relato_cru(texto)) == v4.ECO_GENERICO


def test_o_eco_tem_no_maximo_doze_palavras() -> None:
    longo = "estou com dor " + " ".join(["muito"] * 30)
    assert len(v4.eco_do_relato(v4.relato_cru(longo)).split()) <= 12


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("nao", v4.LEITURA_NAO),
        ("nao nenhum", v4.LEITURA_NAO),
        ("nao tenho nada disso", v4.LEITURA_NAO),
        ("nao tenho falta de ar", v4.LEITURA_NAO),
        ("nenhum", v4.LEITURA_NAO),
        ("nada disso", v4.LEITURA_NAO),
        ("sim", v4.LEITURA_SIM),
        ("sim tenho falta de ar", v4.LEITURA_SIM),
        ("tenho sim", v4.LEITURA_SIM),
        # Lado seguro: duvida, meio-termo, agravamento e incapacidade valem como "sim".
        ("nao sei", v4.LEITURA_SIM),
        ("um pouco", v4.LEITURA_SIM),
        ("talvez", v4.LEITURA_SIM),
        ("nao consigo respirar", v4.LEITURA_SIM),
        ("nao e nao piorou", v4.LEITURA_SIM),
        # Nao deu para entender.
        ("hmm", None),
        ("nao mas agora ele esta com febre", None),
        ("qual o valor do meu boleto de outubro que venceu na semana passada e eu nao paguei ainda", None),
        ("", None),
    ],
)
def test_resposta_ao_alerta_e_do_lado_seguro(plano: str, esperado: str | None) -> None:
    assert v4.resposta_ao_alerta(plano) == esperado


@pytest.mark.parametrize(
    ("plano", "esperado"),
    [
        ("1", "1"),
        ("2", "2"),
        ("opcao 2", "2"),
        ("a 2", "2"),
        ("dois", "2"),
        ("a segunda", "2"),
        ("2 falar com a nossa equipe de saude", "2"),
        ("falar com a equipe de saude", "2"),
        ("equipe de saude", "2"),
        ("quero falar com a equipe", "2"),
        ("1 saber onde marcar consulta pelo plano", "1"),
        ("onde marcar consulta", "1"),
        ("marcar consulta pelo plano", "1"),
        ("opcao 1 por favor", "1"),
        # A mensagem INTEIRA: um numero dentro de outra frase nao e' opcao.
        ("2 dias de dor", None),
        ("tenho 2 filhos", None),
        ("qual o valor do boleto", None),
        ("ok obrigado", None),
        ("3", None),
    ],
)
def test_escolha_da_resposta_so_aceita_a_escolha_inteira(plano: str, esperado: str | None) -> None:
    assert v4.escolha_da_resposta(plano) == esperado


def test_o_codigo_da_pendencia_vai_e_volta_e_recusa_o_malformado() -> None:
    codigo = v4.codigo_pendente(v4.ETAPA_ALERTA, v4.GRUPO_CABECA, v4.POPULACAO_ADULTO, repetida=True)
    assert v4.ler_pendente(codigo) == v4.Pendente(v4.ETAPA_ALERTA, v4.GRUPO_CABECA, v4.POPULACAO_ADULTO, True)
    for ruim in (
        None,
        3,
        "",
        "PERGUNTAR_FICHA_DOR",
        "V4_",
        "V4_ALERTA",
        "V4_NADA:GERAL:adult",
        "V4_ALERTA:X:adult",
    ):
        assert v4.ler_pendente(ruim) is None
    assert v4.ler_pendente("V4_ESCOLHA:GERAL:gestante") is None


@pytest.mark.parametrize(
    ("populacao", "plano", "esperado"),
    [
        ("adult", "dor nas costas", ("adult", v4.GRUPO_GERAL)),
        ("none", "dor nas costas", ("adult", v4.GRUPO_GERAL)),
        ("none", "dor de garganta", ("adult", v4.GRUPO_GERAL)),
        ("none", "dor de cabeca", ("adult", v4.GRUPO_CABECA)),
        ("none", "dor de barriga e vomito", ("adult", v4.GRUPO_GASTRO)),
        ("none", "manchas vermelhas e coceira", ("adult", v4.GRUPO_PELE)),
        ("pediatric", "tosse", ("pediatric", v4.GRUPO_CRIANCA)),
        ("none", "meu filho esta com tosse", ("pediatric", v4.GRUPO_CRIANCA)),
        ("none", "minha filha esta com febre", ("pediatric", v4.GRUPO_CRIANCA)),
    ],
)
def test_populacao_e_grupo_do_sintoma(populacao: str, plano: str, esperado: tuple[str, str]) -> None:
    pop = v4.populacao_da_v4(populacao, plano)
    assert pop is not None
    assert (pop, v4.grupo_do_sintoma(pop, plano)) == esperado


@pytest.mark.parametrize(
    ("populacao", "plano"),
    [
        ("gestante", "dor nas costas"),
        ("mental_health", "estou mal"),
        ("none", "estou gravida e com dor nas costas"),
        ("none", "estou com ansiedade e dor no peito"),
    ],
)
def test_gestante_e_saude_mental_ficam_no_caminho_de_hoje(populacao: str, plano: str) -> None:
    assert v4.populacao_da_v4(populacao, plano) is None


# =================================================================================================
# Os textos: cercas de saida, regua de CLAREZA, nada de conduta, nada de "RASCUNHO", nada de CNS
# =================================================================================================


def _textos_da_v4() -> list[str]:
    textos: list[str] = []
    eco = v4.eco_do_relato("Estou com dor nas costas desde ontem")
    for grupo in v4.ALERTAS:
        textos.append(v4.texto_da_pergunta_de_alerta(grupo, eco))
        textos.append(v4.texto_da_pergunta_de_alerta(grupo, eco, repetida=True))
        textos.append(v4.texto_da_pergunta_de_alerta(grupo, v4.ECO_GENERICO))
    textos.append(v4.texto_final_do_sintoma(eco))
    textos.append(v4.texto_final_do_sintoma(v4.ECO_GENERICO))
    textos.append(v4.texto_da_opcao_1(v4.POPULACAO_ADULTO))
    textos.append(v4.texto_da_opcao_1(v4.POPULACAO_CRIANCA))
    segmentacao = "Ambulatorial + Hospitalar"
    for nome, basica in (("cardiologista", False), ("ginecologista", True)):
        for seg in (None, segmentacao):
            for cobertura in (None, RESPOSTA_COBERTURA_ESPECIFICA):
                textos.append(
                    v4.texto_do_especialista(nome, basica=basica, segmentacao=seg, frase_cobertura=cobertura)
                )
    textos.append(v4.texto_da_carteirinha(v4.fatos_da_carteirinha(_elegibilidade())))
    textos.append(v4.texto_da_carteirinha({"vinculos": []}))
    return textos


@pytest.mark.parametrize("texto", _textos_da_v4())
def test_todo_texto_da_v4_passa_nas_cercas_de_saida_e_na_regua_de_clareza(texto: str) -> None:
    assert motivo_de_canal_nao_confirmado(texto) is None
    assert motivo_de_recusa(texto, "inform") is None
    assert motivo_de_recusa(texto, "collect") is None
    assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None
    assert not _conduta_clinica(texto)
    assert frases_longas(texto) == [], "regua de CLAREZA (DL-0087): frase com mais de 20 palavras"
    assert "RASCUNHO" not in texto.upper()
    assert "helena" not in texto.lower(), "o nome dela acenderia `apresentacao_ja_feita`"
    assert not re.search(r"\bcns\b|cartao nacional", _sem_acento(texto)), "nunca CNS"


def test_o_texto_final_e_o_do_dono_palavra_por_palavra() -> None:
    assert v4.texto_final_do_sintoma("dor nas costas desde ontem") == (
        "Entendi: dor nas costas desde ontem. Eu não avalio sintomas, mas te ajudo com o caminho. "
        "Você prefere: 1) saber onde marcar consulta pelo plano (clínico geral ou pediatra: até 7 dias úteis "
        "pela ANS); ou 2) falar com a nossa equipe de saúde? "
        "Se piorar, procure um pronto-atendimento; se for grave, ligue 192."
    )


def test_o_texto_do_especialista_e_o_da_coluna_hoje_sem_prometer_nada() -> None:
    texto = v4.texto_do_especialista(
        "cardiologista",
        basica=False,
        segmentacao="No cadastro, seu plano consta com segmentação Ambulatorial + Hospitalar.",
        frase_cobertura=None,
    )
    assert texto.startswith(
        "Consulta com cardiologista é consulta de especialista. No cadastro, seu plano consta"
    )
    for trecho in (
        "aplicativo Austa Clínicas",
        "portal do plano",
        "central de atendimento do plano",
        "até 14 dias úteis",
        "não pesquiso especialistas nem marco consulta",
        "Se quiser falar com uma pessoa da equipe, é só me pedir.",
    ):
        assert trecho in texto
    assert not re.search(r"cobert|garant|autoriz", _sem_acento(texto)), "nao promete cobertura"
    assert v4.e_texto_da_v4(texto)


def test_ginecologista_e_consulta_basica_com_7_dias_nao_14() -> None:
    texto = v4.texto_do_especialista("ginecologista", basica=True, segmentacao=None, frase_cobertura=None)
    assert "consulta básica" in texto and "7 dias úteis" in texto and "14" not in texto


def test_o_prazo_de_14_dias_e_rascunho_a_confirmar_e_vem_do_documento_de_conformidade() -> None:
    assert v4.PRAZO_ESPECIALISTA_DIAS_UTEIS == 14
    assert v4.PRAZO_CONSULTA_BASICA_DIAS_UTEIS == 7
    fonte = inspect.getsource(v4)
    assert "RASCUNHO / A CONFIRMAR PELO JURIDICO" in fonte
    assert "rn-currency-review.md" in fonte
    doc = (_REPO / "docs" / "compliance" / "rn-currency-review.md").read_text(encoding="utf-8")
    assert "demais especialidades **14 d.ú.**" in doc


@pytest.mark.parametrize("grupo", list(v4.ALERTAS))
def test_cada_grupo_tem_uma_ou_duas_perguntas_fechadas_de_alerta(grupo: str) -> None:
    alerta = v4.ALERTAS[grupo]
    assert 1 <= len(alerta.perguntas) <= 2
    for pergunta in alerta.perguntas:
        assert pergunta.endswith("?")
        assert frases_longas(pergunta) == []
    assert alerta.sinais


def test_as_perguntas_de_alerta_so_citam_sinais_que_ja_existem_nas_tabelas() -> None:
    """Nenhum sinal clinico novo: cada pergunta so' fala de sinais das tabelas `triage_redflag_*` (aba 3 do
    catalogo v1). O conjunto abaixo e' o vocabulario das tabelas; uma palavra de sinal fora dele reprova."""
    permitido = re.compile(
        r"dor no peito|falta de ar|desmaio|boca torta|fraqueza de um lado do corpo|fala alterada"
        r"|dor de cabeça|pior que você já sentiu|dor muito forte na barriga|sangue no vômito ou nas fezes"
        r"|fezes pretas|rosto|boca|língua|inchados|difícil respirar|dificuldade para respirar|muito mole"
        r"|sonolento|convulsão|febre com manchinhas vermelhas|pouco xixi",
        re.IGNORECASE,
    )
    tabelas = " ".join(
        (DMN_DIR / f"triage_redflag_{p}.dmn").read_text(encoding="utf-8").lower()
        for p in ("adult", "pediatric")
    )
    for grupo, alerta in v4.ALERTAS.items():
        for pergunta in alerta.perguntas:
            restante = permitido.sub("", pergunta)
            # Sobra so' a gramatica (artigos, verbos de ligacao, pontuacao); nenhum sinal desconhecido.
            sobras = re.findall(r"[a-zà-ú]{5,}", restante.lower())
            assert set(sobras) <= {
                "junto",
                "isso",
                "começou",
                "repente",
                "tem",
                "está",
                "estão",
                "teve",
                "ou",
            }, (
                grupo,
                sobras,
            )
    for sinal in ("dor_toracica", "dispneia", "deficit_neurologico", "cefaleia_subita_intensa", "sincope"):
        assert sinal in tabelas
    for sinal in (
        "dificuldade_respiratoria",
        "petequias_febre",
        "convulsao",
        "letargia",
        "sinais_desidratacao",
    ):
        assert sinal in tabelas


# =================================================================================================
# A carteirinha
# =================================================================================================


def test_a_carteirinha_mostra_plano_segmentacao_vigencia_e_ans_com_o_codigo_mascarado() -> None:
    texto = v4.texto_da_carteirinha(v4.fatos_da_carteirinha(_elegibilidade()))
    for linha in (
        "- Código: ******4567",
        "- Plano: PLANO SINTETICO ENFERMARIA",
        "- Segmentação: Ambulatorial + Hospitalar",
        "- Vigência: desde 01/03/2024",
        "- Nº ANS do plano: 999999",
    ):
        assert linha in texto
    assert "aplicativo Austa Clínicas" in texto and "portal do plano" in texto


@pytest.mark.parametrize("codigo", ["12345678901234", "1234567", "4567", "", None, "CNS 700000000000000"])
def test_codigo_nao_mascarado_nunca_chega_ao_texto(codigo: str | None) -> None:
    texto = v4.texto_da_carteirinha(v4.fatos_da_carteirinha(_elegibilidade(carteirinha_mascarada=codigo)))
    assert "não consigo mostrar com segurança" in texto
    assert not re.search(r"\d{5,}", texto.replace("999999", "")), "nenhuma sequencia longa de digitos"
    assert "700000000000000" not in texto


def test_campo_ausente_diz_que_nao_consta_e_nunca_se_completa() -> None:
    view = _elegibilidade(segmentacao=None, registro_ans_plano=None, vigencia_inicio=None, plano=None)
    texto = v4.texto_da_carteirinha(v4.fatos_da_carteirinha(view))
    assert texto.count("não consta no cadastro") == 4


def test_ans_so_entra_como_digitos() -> None:
    assert "Nº ANS do plano: 123456" in v4.texto_da_carteirinha(
        v4.fatos_da_carteirinha(_elegibilidade(registro_ans_plano="12.3456"))
    )
    assert "Nº ANS do plano: não consta" in v4.texto_da_carteirinha(
        v4.fatos_da_carteirinha(_elegibilidade(registro_ans_plano="<script>"))
    )


# =================================================================================================
# O grafo: ESPECIALISTA
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "especialidade"),
    [
        ("Preciso consultar com um cardiologista", "cardiologista"),
        ("Onde tem dermatologista pelo plano?", "dermatologista"),
        ("Quero marcar ortopedista para o meu filho", "ortopedista"),
    ],
)
async def test_especialista_recebe_onde_marcar_prazo_e_oferta_sem_caso(
    mensagem: str, especialidade: str
) -> None:
    for intent in ("information", "scheduling", "clinical_question", "outside_channel", "symptom"):
        conversa = _Conversa()
        resultado = await conversa.diz(mensagem, _classify(intent=intent))
        texto = resultado["response_text"]

        assert conversa.cib.inicios == [], intent
        assert resultado["next_kind"] == "inform"
        assert texto.startswith(f"Consulta com {especialidade} é consulta de especialista."), intent
        for trecho in (
            "aplicativo Austa Clínicas",
            "portal do plano",
            "central de atendimento do plano",
            "14 dias úteis",
            "pessoa da equipe",
        ):
            assert trecho in texto
        assert TRIAGEM_V2_DMN_KEY not in conversa.dmn.chaves()
        assert not re.search(
            r"cobre sim|esta coberto|cobertura garantida|dr\.|dra\.|rua |avenida", _sem_acento(texto)
        )
        assert conversa.whatsapp.enviados == [texto]


async def test_especialista_usa_a_segmentacao_do_cadastro_quando_ha_identidade() -> None:
    fonte = _Fonte()
    conversa = _Conversa(fonte=fonte, com_identidade=True)
    resultado = await conversa.diz("Preciso consultar com um cardiologista")

    assert (
        "No cadastro, seu plano consta com segmentação Ambulatorial + Hospitalar."
        in resultado["response_text"]
    )
    assert fonte.chamadas == [(_REF, "elegibilidade")]
    assert conversa.cib.inicios == []


async def test_especialista_sem_identidade_nao_consulta_nem_cita_segmentacao() -> None:
    fonte = _Fonte()
    conversa = _Conversa(fonte=fonte, com_identidade=False)
    resultado = await conversa.diz("Preciso consultar com um cardiologista")

    assert "segmentação" not in resultado["response_text"]
    assert fonte.chamadas == []


async def test_especialista_com_a_fonte_do_plano_falhando_sai_sem_a_segmentacao() -> None:
    class _FonteQueFalha:
        async def consultar(self, ref: str, subtipo: str) -> Any:
            return None

    conversa = _Conversa(fonte=_FonteQueFalha(), com_identidade=True)
    resultado = await conversa.diz("Preciso consultar com um cardiologista")

    assert resultado["response_text"].startswith("Consulta com cardiologista")
    assert "segmentação" not in resultado["response_text"]


async def test_pergunta_de_cobertura_da_consulta_recebe_tambem_onde_confirmar_sem_prometer() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Meu plano cobre consulta com endocrinologista?", _classify(intent="clinical_question")
    )

    texto = resultado["response_text"]
    assert texto.startswith("Consulta com endocrinologista é consulta de especialista.")
    assert "14 dias úteis" in texto
    assert RESPOSTA_COBERTURA_ESPECIFICA in texto
    assert conversa.cib.inicios == []


async def test_cobertura_de_exame_nao_vira_especialista() -> None:
    """ "cobre ressonancia?" segue o caminho de hoje (cobertura deterministica da consulta do plano)."""
    conversa = _Conversa(fonte=_Fonte(), com_identidade=True)
    resultado = await conversa.diz(
        "Meu plano cobre ressonância?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="elegibilidade"),
    )

    texto = resultado["response_text"]
    assert "14 dias" not in texto and "consulta de especialista" not in texto
    assert RESPOSTA_COBERTURA_ESPECIFICA in texto
    assert conversa.cib.inicios == []


async def test_especialista_com_pedido_de_pessoa_abre_o_caso_como_sempre() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Quero falar com uma pessoa sobre o cardiologista", _classify(intent="human_request")
    )

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert conversa.motivos == ["solicitacao_humano"]
    assert "14 dias" not in resultado["response_text"]


async def test_especialista_com_sintoma_vai_para_o_sintoma_nao_para_o_texto_do_especialista() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Estou com dor no joelho e preciso de um ortopedista", _classify(intent="symptom", population="adult")
    )

    assert resultado["next_kind"] == "collect"
    assert "14 dias" not in resultado["response_text"]
    assert "responda sim ou não" in resultado["response_text"].lower()


async def test_especialista_com_alarme_continua_escalando() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Estou com dor no peito, preciso de um cardiologista",
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert "192" in resultado["response_text"]
    assert "14 dias" not in resultado["response_text"]


@pytest.mark.parametrize(
    "mensagem", ["Preciso de um psiquiatra", "Quero marcar fisioterapia", "Preciso de um psicólogo"]
)
async def test_especialidades_fora_do_lexico_nao_recebem_o_texto_de_14_dias(mensagem: str) -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(mensagem, _classify(intent="outside_channel"))

    assert "14 dias" not in (resultado.get("response_text") or "")
    assert "consulta de especialista" not in (resultado.get("response_text") or "")


# =================================================================================================
# O grafo: CARTEIRINHA
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "extracao"),
    [
        (
            "Qual o número da minha carteirinha?",
            _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
        ),
        ("Qual o número da minha carteirinha?", _classify(intent="information")),
        ("Me manda minha carteirinha", _classify(intent="information")),
        ("Perdi a minha carteirinha", _classify(intent="outside_channel")),
    ],
)
async def test_carteirinha_mostra_os_dados_mascarados_e_manda_o_cartao_para_o_aplicativo(
    mensagem: str, extracao: str
) -> None:
    fonte = _Fonte()
    conversa = _Conversa(fonte=fonte, com_identidade=True)
    resultado = await conversa.diz(mensagem, extracao)

    texto = resultado["response_text"]
    assert texto == v4.texto_da_carteirinha(v4.fatos_da_carteirinha(_elegibilidade()))
    assert "Código: ******4567" in texto
    for rotulo in ("Plano:", "Segmentação:", "Vigência: desde 01/03/2024", "Nº ANS do plano: 999999"):
        assert rotulo in texto
    assert "aplicativo Austa Clínicas" in texto and "portal do plano" in texto
    assert not re.search(r"\bcns\b|nascimento|cpf|\d{9,}", _sem_acento(texto))
    assert conversa.cib.inicios == []


async def test_carteirinha_sem_identidade_responde_o_texto_fixo_de_sempre() -> None:
    conversa = _Conversa(fonte=_Fonte(), com_identidade=False)
    resultado = await conversa.diz(
        "Qual o número da minha carteirinha?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
    )

    assert "Código:" not in resultado["response_text"]
    assert "Não consegui confirmar seus dados" in resultado["response_text"]


async def test_carteirinha_com_codigo_nao_mascarado_na_fonte_nunca_o_mostra() -> None:
    fonte = _Fonte(_elegibilidade(carteirinha_mascarada="00112233445566"))
    conversa = _Conversa(fonte=fonte, com_identidade=True)
    resultado = await conversa.diz(
        "Qual o número da minha carteirinha?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
    )

    assert "00112233445566" not in resultado["response_text"]
    assert "não consigo mostrar com segurança" in resultado["response_text"]


async def test_carteirinha_com_cobranca_no_texto_continua_cobranca() -> None:
    """ "2a via do boleto" nao e' pedido de carteirinha: o roteador de cobranca decide, nao a v4."""
    assert not v4.pede_carteirinha("preciso da segunda via do boleto da carteirinha")
    assert v4.pede_carteirinha("qual o numero da minha carteirinha")


# =================================================================================================
# O grafo: SINTOMA LEVE (o exemplo do dono)
# =================================================================================================


async def test_dor_nas_costas_so_recebe_pergunta_de_alerta_e_usa_o_que_a_pessoa_disse() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)

    texto = resultado["response_text"]
    assert resultado["next_kind"] == "collect" and resultado["response_kind"] == "collect"
    assert texto.startswith("Entendi: dor nas costas desde ontem.")
    assert "dor no peito, falta de ar, desmaio, boca torta ou fraqueza de um lado do corpo?" in texto
    assert texto.endswith("Responda sim ou não.")
    # So' alerta: nenhuma ficha (local/intensidade/inicio), nenhum caminho, nenhum prazo.
    assert not re.search(r"em que parte|fraca, média|começou de repente|dias úteis|aplicativo|portal", texto)
    assert conversa.cib.inicios == []
    assert "triage_redflag_adult" in conversa.dmn.chaves(), "a tabela de alarme roda ANTES da pergunta"
    assert conversa.memoria["coleta_pendente"] == "V4_ALERTA:GERAL:adult"
    assert conversa.memoria["coleta_contexto"] == "Estou com dor nas costas desde ontem"


async def test_dor_nas_costas_com_nao_recebe_a_resposta_exata_do_dono_e_a_opcao_1_o_caminho() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    final = await conversa.diz("Não")

    assert final["response_text"] == v4.texto_final_do_sintoma("dor nas costas desde ontem")
    assert final["response_text"].startswith("Entendi: dor nas costas desde ontem. Eu não avalio sintomas")
    for trecho in (
        "1) saber onde marcar consulta pelo plano (clínico geral ou pediatra: até 7 dias úteis pela ANS)",
        "2) falar com a nossa equipe de saúde?",
        "Se piorar, procure um pronto-atendimento; se for grave, ligue 192.",
    ):
        assert trecho in final["response_text"]
    assert final["next_kind"] == "inform" and conversa.cib.inicios == []
    assert conversa.memoria["coleta_pendente"] == "V4_ESCOLHA:GERAL:adult"

    um = await conversa.diz("1")
    assert um["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO
    assert "aplicativo Austa Clínicas" in um["response_text"] and "7 dias úteis" in um["response_text"]
    assert "clínico geral" in um["response_text"]
    assert not re.search(r"pode ser|virose|infec|tome ", _sem_acento(um["response_text"]))
    assert conversa.cib.inicios == []
    assert conversa.memoria["coleta_pendente"] is None and conversa.memoria["coleta_contexto"] == ""


async def test_a_opcao_2_abre_o_caso_com_resumo_legivel_so_do_relato_desta_queixa() -> None:
    # A conversa JA tinha outro assunto na memoria de coleta (cobranca): nao pode vazar para o resumo.
    conversa = _Conversa(
        inicial={
            "coleta_contexto": "boleto vencido de setembro | coparticipacao de agosto",
            "coleta_rodadas": 0,
        }
    )
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    await conversa.diz("Não")
    caso = await conversa.diz("2")

    assert conversa.motivos == ["intencao_clinica"]
    assert caso["escalation_severidade"] == "moderada"
    resumo = conversa.cib.inicios[0]["resumo_contexto"]
    assert 'A pessoa relatou (queixa da própria pessoa): "Estou com dor nas costas desde ontem".' in resumo
    assert "dor no peito, falta de ar, desmaio" in resumo
    assert "a resposta foi não" in resumo and "escolheu falar com a equipe de saúde" in resumo
    assert "não avaliou sintomas nem deu orientação clínica" in resumo
    assert not re.search(r"boleto|coparticipa|setembro|agosto", _sem_acento(resumo))
    assert "*" not in resumo and "ficha da triagem" not in resumo.lower() and "[" not in resumo
    assert "equipe de saúde" in caso["response_text"]


async def test_resposta_sim_a_pergunta_de_alerta_escala_com_o_192() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    resultado = await conversa.diz("Sim")

    assert (
        resultado["escalation_motivo"] == "red_flag_clinico" and resultado["escalation_severidade"] == "grave"
    )
    assert conversa.motivos == ["red_flag_clinico"]
    assert "192" in resultado["response_text"]
    resumo = conversa.cib.inicios[0]["resumo_contexto"]
    assert "respondeu sim" in resumo and "dor nas costas desde ontem" in resumo


@pytest.mark.parametrize(
    "resposta", ["Não sei", "Um pouco", "não consigo respirar direito", "Nao, mas piorou"]
)
async def test_duvida_ou_agravamento_na_resposta_escala_do_lado_seguro(resposta: str) -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    resultado = await conversa.diz(resposta)

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert "192" in resultado["response_text"]


async def test_resposta_que_nao_se_entende_pergunta_de_novo_uma_vez_e_depois_abre_o_caso_por_precaucao() -> (
    None
):
    conversa = _Conversa()
    await conversa.diz(
        "Estou com dor de cabeça", _classify(intent="symptom", population="adult", intensidade="leve")
    )
    segunda = await conversa.diz("hmm")

    assert segunda["next_kind"] == "collect"
    assert segunda["response_text"].startswith("Só para eu entender: a dor de cabeça começou de repente")
    assert conversa.cib.inicios == []

    terceira = await conversa.diz("sei lá")
    assert terceira["escalation_motivo"] == "intencao_clinica"
    assert "não ficou claro" in conversa.cib.inicios[0]["resumo_contexto"]


async def test_outro_assunto_no_meio_da_pergunta_segue_o_caminho_de_sempre_e_limpa_a_pergunta() -> None:
    conversa = _Conversa(fonte=_Fonte(), com_identidade=True)
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    resultado = await conversa.diz(
        "Qual o número da minha carteirinha, por favor, preciso agora para a consulta de amanhã?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
    )

    assert "Código: ******4567" in resultado["response_text"]
    assert conversa.memoria["coleta_pendente"] is None


async def test_o_numero_solto_sem_oferta_recente_nao_abre_caso() -> None:
    """A oferta 1/2 vale SO' no turno seguinte: depois de outro assunto, "2" nao e' a opcao 2."""
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    await conversa.diz("Não")
    await conversa.diz("Obrigado, tchau")
    resultado = await conversa.diz("2")

    assert conversa.cib.inicios == []
    assert "encaminhamos" not in (resultado.get("response_text") or "")


async def test_filho_com_tosse_usa_as_perguntas_da_crianca_e_o_resumo_diz_que_e_do_filho() -> None:
    conversa = _Conversa()
    primeira = await conversa.diz("Meu filho está com tosse", _SINTOMA_CRIANCA)

    assert primeira["response_text"].startswith("Entendi: seu filho está com tosse.")
    assert (
        "dificuldade para respirar, muito mole ou sonolento(a), ou teve convulsão?"
        in primeira["response_text"]
    )
    assert "febre com manchinhas vermelhas na pele, ou está com pouco xixi?" in primeira["response_text"]
    assert conversa.memoria["coleta_pendente"] == "V4_ALERTA:CRIANCA:pediatric"

    final = await conversa.diz("Não")
    assert final["response_text"].startswith("Entendi: seu filho está com tosse. Eu não avalio sintomas")

    um = await conversa.diz("1")
    assert (
        um["response_text"] == RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA and "pediatra" in um["response_text"]
    )

    # Outra conversa: a opcao 2 de um filho.
    outra = _Conversa()
    await outra.diz("Meu filho está com tosse", _SINTOMA_CRIANCA)
    await outra.diz("Não")
    await outra.diz("2")
    resumo = outra.cib.inicios[0]["resumo_contexto"]
    assert 'queixa do filho(a) ou bebê): "Meu filho está com tosse"' in resumo
    assert "dificuldade para respirar" in resumo


@pytest.mark.parametrize(
    "extracao",
    [
        # O que o modelo pode devolver para "2" olhando a conversa: o codigo/a intensidade da queixa anterior.
        _classify(intent="symptom", population="pediatric", sintoma_codigo="tosse", intensidade="grave"),
        _classify(
            intent="symptom",
            population="pediatric",
            sintoma_codigo="dificuldade_respiratoria",
            intensidade="desconhecida",
        ),
        _classify(intent="symptom", population="pediatric", intensidade="grave"),
    ],
)
async def test_a_opcao_2_do_filho_com_tosse_e_intencao_clinica_mesmo_se_o_modelo_herdar_a_queixa(
    extracao: str,
) -> None:
    """Aceite de 16/10/2026 (`helena_v4`, segmento 7, run 00d355): 'Meu filho esta com tosse' -> 'Nao' -> '2'
    saiu `red_flag_clinico` grave porque o modelo devolveu sintoma/intensidade da queixa anterior para o
    '2' e a tabela pediatrica, conservadora, escalou. A escolha pura nao roda a tabela."""
    conversa = _Conversa()
    await conversa.diz("Meu filho está com tosse", _SINTOMA_CRIANCA)
    await conversa.diz("Não")
    chamadas_antes = len(conversa.dmn.chaves())
    resultado = await conversa.diz("2", extracao)

    assert conversa.motivos == ["intencao_clinica"]
    assert resultado["escalation_motivo"] == "intencao_clinica"
    assert resultado["escalation_severidade"] == "moderada"
    novas = conversa.dmn.chaves()[chamadas_antes:]
    assert not [c for c in novas if c.startswith("triage_redflag")]


async def test_escolha_pura_com_sinal_de_alarme_na_mensagem_continua_pela_tabela() -> None:
    """A excecao so' vale para a mensagem que e' SO' a escolha: 'opcao 2, ele esta roxo' e' alarme."""
    conversa = _Conversa()
    await conversa.diz("Meu filho está com tosse", _SINTOMA_CRIANCA)
    await conversa.diz("Não")
    resultado = await conversa.diz(
        "2, ele está roxo e não respira",
        _classify(
            intent="symptom", population="pediatric", sintoma_codigo="cianose", intensidade="grave"
        ),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"


@pytest.mark.parametrize(
    ("mensagem", "pergunta"),
    [
        ("Estou com dor de cabeça", "A dor de cabeça começou de repente e é a pior que você já sentiu?"),
        (
            "Estou com dor de barriga e vômito",
            "Tem dor muito forte na barriga, sangue no vômito ou nas fezes",
        ),
        ("Estou com coceira e manchas na pele", "O rosto, a boca ou a língua estão inchados"),
    ],
)
async def test_o_tipo_de_queixa_escolhe_as_perguntas_de_alerta(mensagem: str, pergunta: str) -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        mensagem, _classify(intent="symptom", population="adult", intensidade="leve")
    )

    assert pergunta in resultado["response_text"]
    assert conversa.cib.inicios == []


async def test_a_resposta_curta_nao_depende_do_modelo_de_redacao() -> None:
    """Sem texto livre de modelo: so' a classificacao chama o modelo (nada de resumo nem rascunho)."""
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    await conversa.diz("Não")
    await conversa.diz("2")

    assert len(conversa.modelo.prompts) == 3, "so' a classificacao de cada turno"
    assert all("Resuma em 1-2 frases" not in p for p in conversa.modelo.prompts)


# =================================================================================================
# As travas de seguranca continuam ANTES e intactas
# =================================================================================================


async def test_dor_no_peito_escala_na_hora_sem_pergunta() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Estou com dor no peito",
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert conversa.motivos == ["red_flag_clinico"]
    assert "192" in resultado["response_text"]


async def test_alarme_no_meio_da_pergunta_de_alerta_escala_pela_tabela() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    resultado = await conversa.diz(
        "Agora estou com dor no peito e falta de ar",
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert conversa.memoria["coleta_pendente"] is None, "a pergunta velha nao fica esperando"


async def test_risco_psicossocial_continua_antes_de_tudo() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Estou com dor nas costas e não quero mais viver",
        _classify(intent="symptom", population="adult", psychosocial_risk=True),
    )

    assert resultado["escalation_motivo"] == "risco_psicossocial"
    assert conversa.motivos == ["risco_psicossocial"]


async def test_recem_nascido_com_sinal_continua_escalando() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz(
        "Meu bebê recém-nascido está chorando muito e não quer mamar",
        _classify(intent="symptom", population="pediatric", idade_meses=0),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert "192" in resultado["response_text"]


async def test_pedido_de_pessoa_continua_abrindo_caso() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Quero falar com uma pessoa", _classify(intent="human_request"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert conversa.motivos == ["solicitacao_humano"]


async def test_pedido_de_pessoa_no_meio_da_pergunta_de_alerta_nao_exige_responder() -> None:
    conversa = _Conversa()
    await conversa.diz(
        "Estou com dor de cabeça", _classify(intent="symptom", population="adult", intensidade="leve")
    )
    resultado = await conversa.diz("Quero falar com uma pessoa", _classify(intent="human_request"))

    assert resultado["escalation_motivo"] in ("intencao_clinica", "solicitacao_humano")
    assert len(conversa.cib.inicios) == 1


@pytest.mark.parametrize(
    "extracao",
    [
        # O que o modelo real devolveu no aceite de 16/10/2026: o codigo da queixa ANTERIOR.
        _classify(
            intent="symptom",
            population="adult",
            sintoma_codigo="cefaleia_subita_intensa",
            intensidade="desconhecida",
        ),
        _classify(
            intent="human_request",
            population="adult",
            sintoma_codigo="cefaleia_subita_intensa",
            intensidade="desconhecida",
        ),
        _classify(intent="symptom", population="adult", intensidade="desconhecida"),
    ],
)
async def test_pedido_de_pessoa_no_alerta_abre_o_caso_clinico_sem_rodar_a_tabela_de_alarme(
    extracao: str,
) -> None:
    """Aceite de 16/10/2026 (`helena_v4`, segmento 11): 'Quero falar com uma pessoa' depois de
    'Estou com dor de cabeca' saiu `red_flag_clinico` com o texto de emergencia, porque o modelo devolveu
    o codigo da queixa anterior e a tabela, sem intensidade, escalou. A mensagem, sozinha, so' pede uma
    pessoa."""
    conversa = _Conversa()
    await conversa.diz(
        "Estou com dor de cabeça", _classify(intent="symptom", population="adult", intensidade="leve")
    )
    chamadas_antes = len(conversa.dmn.chaves())
    resultado = await conversa.diz("Quero falar com uma pessoa", extracao)

    assert conversa.motivos == ["intencao_clinica"], "um caso, clinico (enfermagem), nunca red_flag"
    assert resultado["escalation_motivo"] == "intencao_clinica"
    assert resultado["escalation_severidade"] == "moderada"
    novas = conversa.dmn.chaves()[chamadas_antes:]
    assert not [c for c in novas if c.startswith("triage_redflag")], "a tabela de alarme nao roda aqui"
    resumo = conversa.cib.inicios[0]["resumo_contexto"]
    assert 'A pessoa relatou (queixa da própria pessoa): "Estou com dor de cabeça".' in resumo
    assert "em vez de responder, a pessoa pediu para falar com uma pessoa da equipe" in resumo
    assert "não avaliou sintomas nem deu orientação clínica" in resumo


async def test_pedido_de_pessoa_junto_com_sinal_de_alarme_continua_red_flag() -> None:
    """A excecao so' vale para a mensagem SEM queixa: 'dor no peito, quero falar com uma pessoa' e' alarme."""
    conversa = _Conversa()
    await conversa.diz(
        "Estou com dor de cabeça", _classify(intent="symptom", population="adult", intensidade="leve")
    )
    resultado = await conversa.diz(
        "Estou com dor no peito, quero falar com uma pessoa",
        _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert conversa.motivos == ["red_flag_clinico"]


async def test_sem_a_pergunta_de_alerta_pendente_o_pedido_de_pessoa_e_o_de_sempre() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Quero falar com uma pessoa", _classify(intent="human_request"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"


def test_o_resumo_do_pedido_de_pessoa_no_alerta_e_legivel() -> None:
    codigo = v4.codigo_pendente(v4.ETAPA_CASO_PEDIU_PESSOA, v4.GRUPO_GERAL, v4.POPULACAO_ADULTO)
    assert v4.ler_pendente(codigo) is not None
    resumo = v4.resumo_do_caso(codigo, "Estou com dor nas costas")
    assert resumo is not None and "pediu para falar com uma pessoa da equipe" in resumo


@pytest.mark.parametrize(
    "extracao",
    [
        _classify(intent="symptom", population="gestante", idade_gestacional_semanas=30, intensidade="leve"),
        _classify(intent="symptom", population="pediatric", idade_meses=1, intensidade="leve"),
    ],
)
async def test_gestante_e_bebe_pequeno_ficam_no_caminho_de_hoje(extracao: str) -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com um desconforto leve", extracao)

    assert "responda sim ou não" not in (resultado.get("response_text") or "").lower()
    assert not str(conversa.memoria.get("coleta_pendente") or "").startswith("V4_")


# =================================================================================================
# A v4 manda na v2; chave DESLIGADA = o de antes
# =================================================================================================


async def test_com_as_duas_chaves_ligadas_a_v4_manda_e_a_tabela_da_v2_nao_e_consultada() -> None:
    conversa = _Conversa(v4=True, v2=True)
    await conversa.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    await conversa.diz("Não")
    await conversa.diz("Posso tomar dipirona?", _classify(intent="clinical_question"))

    assert TRIAGEM_V2_DMN_KEY not in conversa.dmn.chaves()
    assert conversa.motivos == ["intencao_clinica"], "a pergunta clinica volta a escalar como antes da v2"


_AMOSTRA = [
    ("Preciso consultar com um cardiologista", _classify(intent="information")),
    ("Onde tem dermatologista pelo plano?", _classify(intent="information")),
    ("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO),
    ("Meu filho está com tosse", _SINTOMA_CRIANCA),
    ("Posso tomar dipirona?", _classify(intent="clinical_question")),
    ("Quero falar com uma pessoa", _classify(intent="human_request")),
    ("Oi", _classify(intent="greeting")),
    (
        "Qual o número da minha carteirinha?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
    ),
]


@pytest.mark.parametrize(("mensagem", "extracao"), _AMOSTRA)
async def test_chave_desligada_e_byte_a_byte_o_grafo_sem_a_chave(mensagem: str, extracao: str) -> None:
    """`helena_v4_enabled=False` (explicito) e o argumento ausente produzem EXATAMENTE a mesma saida, os
    mesmos prompts e as mesmas chamadas as tabelas; e nenhum metodo da v4 e' alcancado."""
    explicito = _Conversa(v4=False, fonte=_Fonte(), com_identidade=True)
    sem_arg = _Conversa(v4=False, fonte=_Fonte(), com_identidade=True)

    def _grafo_sem_argumento(self: _Conversa) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=self.dmn,  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=FakeStartAuditSink(),
            whatsapp=self.whatsapp,
            historico_enabled=True,
            coleta_enabled=True,
            consultas_plano=self.fonte,
            relogio=lambda: datetime(2026, 10, 16, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    def _nao_deve_ser_chamado(*_: Any, **__: Any) -> None:
        raise AssertionError("a chave esta desligada: a v4 nao pode ser alcancada")

    with (
        mock.patch.object(HelenaGraph, "_v4_antes_dos_gatilhos", _nao_deve_ser_chamado),
        mock.patch.object(HelenaGraph, "_v4_sintoma_leve", _nao_deve_ser_chamado),
        mock.patch.object(_Conversa, "_grafo", _grafo_sem_argumento),
    ):
        a = await sem_arg.diz(mensagem, extracao)
    with (
        mock.patch.object(HelenaGraph, "_v4_antes_dos_gatilhos", _nao_deve_ser_chamado),
        mock.patch.object(HelenaGraph, "_v4_sintoma_leve", _nao_deve_ser_chamado),
    ):
        b = await explicito.diz(mensagem, extracao)

    for campo in (
        "response_text",
        "response_kind",
        "next_kind",
        "escalation_motivo",
        "intent",
        "coleta_pendente",
    ):
        assert a.get(campo) == b.get(campo), campo
    assert sem_arg.modelo.prompts == explicito.modelo.prompts
    assert sem_arg.dmn.chaves() == explicito.dmn.chaves()
    assert sem_arg.motivos == explicito.motivos


async def test_chave_desligada_o_especialista_e_o_sintoma_seguem_como_hoje() -> None:
    conversa = _Conversa(v4=False)
    especialista = await conversa.diz(
        "Preciso consultar com um cardiologista", _classify(intent="information")
    )
    assert "14 dias úteis" not in (especialista.get("response_text") or "")

    sintoma = _Conversa(v4=False)
    resultado = await sintoma.diz("Estou com dor nas costas desde ontem", _SINTOMA_ADULTO)
    assert "responda sim ou não" not in (resultado.get("response_text") or "").lower()
    assert "Entendi: dor nas costas" not in (resultado.get("response_text") or "")
    assert not str(sintoma.memoria.get("coleta_pendente") or "").startswith("V4_")


async def test_chave_desligada_a_carteirinha_continua_a_de_antes() -> None:
    conversa = _Conversa(v4=False, fonte=_Fonte(), com_identidade=True)
    resultado = await conversa.diz(
        "Qual o número da minha carteirinha?",
        _classify(intent=INTENT_CONSULTA_PLANO, consulta_subtipo="carteirinha"),
    )

    assert "Nº ANS" not in resultado["response_text"]
    assert "Código:" not in resultado["response_text"]
    assert "carteirinha: ******4567" in resultado["response_text"]


async def test_pendencia_da_v4_de_uma_conversa_antiga_e_descartada_com_a_chave_desligada() -> None:
    conversa = _Conversa(
        v4=False,
        inicial={"coleta_pendente": "V4_ESCOLHA:GERAL:adult", "coleta_contexto": "dor", "coleta_rodadas": 1},
    )
    resultado = await conversa.diz("2", _classify(intent="information"))

    assert conversa.cib.inicios == []
    assert "encaminhamos" not in (resultado.get("response_text") or "")
    assert not str(conversa.memoria.get("coleta_pendente") or "").startswith("V4_")


# =================================================================================================
# A fiacao da chave: nasce desligada em todo lugar; so' o dev liga, pelo Terraform
# =================================================================================================


def test_a_chave_nasce_desligada_no_grafo_na_fabrica_e_nas_settings() -> None:
    from maezo.agents.helena.graph import build
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    assert inspect.signature(HelenaGraph.__init__).parameters["helena_v4_enabled"].default is False
    assert 'cfg.get("helena_v4_enabled", False) is True' in inspect.getsource(build)
    assert WhatsAppWebhookSettings.model_fields["helena_v4"].default is False
    alias = WhatsAppWebhookSettings.model_fields["helena_v4"].validation_alias
    assert "MAEZO_HELENA_V4" in getattr(alias, "choices", ())
    assert HelenaDispatcher.__dataclass_fields__["helena_v4_enabled"].default is False
    assert '"helena_v4_enabled": self.helena_v4_enabled' in inspect.getsource(
        HelenaDispatcher._compile_turn_graph
    )
    servico = (_REPO / "src/maezo/platform/webhooks/service.py").read_text(encoding="utf-8")
    assert "helena_v4_enabled=settings.helena_v4" in servico


@pytest.mark.parametrize("ligado", [True, False])
def test_o_despachante_passa_a_chave_ao_grafo(ligado: bool) -> None:
    from maezo.platform.webhooks.whatsapp import dispatch as dispatch_module

    capturado: dict[str, Any] = {}

    class _Grafo:
        def compile(self, **_: Any) -> str:
            return "compilado"

    def _build(cfg: dict[str, Any]) -> _Grafo:
        capturado.update(cfg)
        return _Grafo()

    nada: Any = None
    with mock.patch.object(dispatch_module, "build", _build):
        despachante = dispatch_module.HelenaDispatcher(
            tenant_id="amh",
            inference=nada,
            dmn=nada,
            cibseven=nada,
            whatsapp_client=nada,
            pseudonymizer=nada,
            audit_sink=nada,
            helena_v4_enabled=ligado,
        )
        despachante._compile_turn_graph(nada)

    assert capturado["helena_v4_enabled"] is ligado
    assert capturado["triagem_v2_enabled"] is False


def test_a_chave_so_e_ligada_no_dev_pelo_terraform() -> None:
    envs = _REPO / "deploy" / "aws-ecs" / "envs"
    dev = envs / "dev-sa-east-1"
    tfvars = (dev / "amh-interop.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^helena_v4 = true$", tfvars, re.MULTILINE)
    receptor = (dev / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    assert '{ name = "MAEZO_HELENA_V4", value = tostring(var.helena_v4) }' in receptor
    variaveis = (dev / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "helena_v4"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "helena_v4" not in arquivo.read_text(encoding="utf-8"), arquivo


def test_o_roteiro_de_aceite_helena_v4_existe_e_todo_padrao_compila() -> None:
    assert "helena_v4" in aceite.ROTEIROS
    aceite.validar(aceite.ROTEIROS["helena_v4"])
    textos = [p.texto for s in aceite.HELENA_V4 for p in s.passos]
    for exemplo in (
        "Preciso consultar com um cardiologista",
        "Onde tem dermatologista pelo plano?",
        "Estou com dor nas costas desde ontem",
        "Meu filho está com tosse",
        "Qual o número da minha carteirinha?",
    ):
        assert exemplo in textos
    # Cada segmento declara o criterio "RESOLVEU?" (a licao de 15/10/2026).
    assert all(s.resolveu for s in aceite.HELENA_V4)
    # E nenhum dos roteiros ja' existentes foi tocado: continuam com o mesmo conjunto de segmentos.
    assert [s.nome for s in aceite.DONO_V1][:2] == ["1-plano-e-cobranca", "2-pedido-de-atendente"]
    assert [s.nome for s in aceite.CANCELAMENTO_V1] == [
        "1-titular-pf-pede-cancelamento",
        "2-titular-pf-desiste",
    ]


def test_o_roteiro_reprova_a_resposta_que_nao_resolve() -> None:
    """O criterio e' o CONTEUDO util: texto generico sem o que a pessoa disse nao passa."""
    costas = aceite.HELENA_V4[5].passos
    generico = (
        "Eu não avalio sintomas, então não consigo dizer se você precisa de médico. "
        "Você pode marcar consulta "
        "com um médico da rede do plano. Se piorar, procure um pronto-atendimento."
    )
    assert aceite.avaliar_passo(costas[0], [("helena", generico)], [], None)
    assert aceite.avaliar_passo(costas[1], [("helena", generico)], [], None)
    cardiologista = aceite.HELENA_V4[0].passos[0]
    assert aceite.avaliar_passo(
        cardiologista, [("helena", "Posso ajudar com isso. Me conte mais.")], [], None
    )
    carteirinha = aceite.HELENA_V4[4].passos[0]
    assert aceite.avaliar_passo(
        carteirinha, [("helena", "Código: 0011223344556677. CNS 700000000000000")], [], None
    )
    # Promessa de cobertura e nome de medico reprovam o especialista.
    promessa = (
        "Consulta com cardiologista é consulta de especialista. O plano cobre sim. Dra. Ana atende na rua X."
    )
    assert any(
        f.startswith("nao_deve")
        for f in aceite.avaliar_passo(cardiologista, [("helena", promessa)], [], None)
    )
